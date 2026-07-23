from __future__ import annotations

import hashlib
import json
import re
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from sd_agent.auth.service import AuthenticatedUser

FILE_ID_PATTERN = re.compile(r"^file_[A-Za-z0-9_-]{8,128}$")


class CommandState(StrEnum):
    PENDING = "pending"
    TEABLE_WRITTEN = "teable_written"
    COMMITTED = "committed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class SubmissionInput:
    task_id: int
    content: str
    progress: int
    file_ids: tuple[str, ...]
    on_behalf_of: int | None
    task_revision: int

    def normalized(self) -> SubmissionInput:
        return SubmissionInput(
            task_id=self.task_id,
            content=" ".join(self.content.split()),
            progress=self.progress,
            file_ids=tuple(sorted(set(self.file_ids))),
            on_behalf_of=self.on_behalf_of,
            task_revision=self.task_revision,
        )


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    record_id: str
    task_id: int
    unit_id: int
    primary_owner_id: int
    progress: int
    revision: int


@dataclass(frozen=True, slots=True)
class ProgressWrite:
    record_id: str
    log_id: int


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    submission_id: str
    log_id: int
    state: str = "committed"


@dataclass(frozen=True, slots=True)
class CommandSnapshot:
    command_id: str
    idempotency_key: str
    person_id: int
    task_id: int
    payload_hash: str
    state: CommandState
    teable_record_id: str | None = None
    result: SubmissionResult | None = None
    last_error_code: str | None = None


@dataclass(frozen=True, slots=True)
class AuditFacts:
    request_id: str
    person_id: int
    roles: tuple[str, ...]
    task_id: int
    file_ids: tuple[str, ...]
    ip: str | None
    user_agent: str | None


class SubmissionError(ValueError):
    def __init__(
        self, code: str, message: str, status_code: int, *, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


class SubmissionPersistence(Protocol):
    def task_lock(self, task_id: int) -> AbstractAsyncContextManager[None]: ...

    async def reserve(
        self,
        *,
        idempotency_key: str,
        person_id: int,
        task_id: int,
        payload_hash: str,
        now: datetime,
    ) -> CommandSnapshot: ...

    async def mark_teable_written(
        self,
        command_id: str,
        record_id: str,
        *,
        now: datetime,
    ) -> None: ...

    async def reject(self, command_id: str, code: str, *, now: datetime) -> None: ...

    async def complete(
        self,
        command_id: str,
        result: SubmissionResult,
        audit: AuditFacts,
        *,
        now: datetime,
    ) -> None: ...


class SubmissionGateway(Protocol):
    async def get_task(self, task_id: int) -> TaskSnapshot | None: ...

    async def files_are_clean(
        self,
        file_ids: tuple[str, ...],
        *,
        task_id: int,
        person_id: int,
    ) -> bool: ...

    async def find_progress_by_command(self, command_id: str) -> ProgressWrite | None: ...

    async def append_progress(
        self,
        command_id: str,
        request: SubmissionInput,
        *,
        reporter_id: int,
        now: datetime,
    ) -> ProgressWrite: ...

    async def update_task_progress(
        self,
        task: TaskSnapshot,
        *,
        progress: int,
        next_revision: int,
        idempotency_key: str,
    ) -> TaskSnapshot: ...


class SubmissionService:
    def __init__(self, *, persistence: SubmissionPersistence, gateway: SubmissionGateway) -> None:
        self._persistence = persistence
        self._gateway = gateway

    async def submit(
        self,
        *,
        user: AuthenticatedUser,
        request: SubmissionInput,
        idempotency_key: UUID,
        request_id: str,
        ip: str | None,
        user_agent: str | None,
        now: datetime,
    ) -> SubmissionResult:
        current_time = _require_utc(now)
        normalized = request.normalized()
        payload_hash = _payload_hash(normalized)
        async with self._persistence.task_lock(normalized.task_id):
            command = await self._persistence.reserve(
                idempotency_key=str(idempotency_key),
                person_id=user.person.person_id,
                task_id=normalized.task_id,
                payload_hash=payload_hash,
                now=current_time,
            )
            self._validate_command(
                command, user=user, request=normalized, payload_hash=payload_hash
            )
            if command.state is CommandState.COMMITTED and command.result is not None:
                return command.result
            if command.state is CommandState.REJECTED:
                raise SubmissionError(
                    command.last_error_code or "SUBMISSION_REJECTED",
                    "该提交此前已被拒绝，请修正后使用新的幂等键",
                    409,
                )

            task = await self._gateway.get_task(normalized.task_id)
            try:
                await self._validate_request(normalized, user=user, task=task)
            except SubmissionError as exc:
                await self._persistence.reject(command.command_id, exc.code, now=current_time)
                raise
            assert task is not None

            progress_write = await self._gateway.find_progress_by_command(command.command_id)
            if progress_write is None:
                progress_write = await self._gateway.append_progress(
                    command.command_id,
                    normalized,
                    reporter_id=user.person.person_id,
                    now=current_time,
                )
            if command.state is CommandState.PENDING:
                await self._persistence.mark_teable_written(
                    command.command_id,
                    progress_write.record_id,
                    now=current_time,
                )

            already_updated = (
                task.revision == normalized.task_revision + 1
                and task.progress == normalized.progress
            )
            if not already_updated:
                updated = await self._gateway.update_task_progress(
                    task,
                    progress=normalized.progress,
                    next_revision=task.revision + 1,
                    idempotency_key=str(idempotency_key),
                )
                if updated.revision != task.revision + 1 or updated.progress != normalized.progress:
                    raise SubmissionError(
                        "TEABLE_WRITE_UNCONFIRMED",
                        "外部数据写入结果无法确认，请稍后使用原幂等键重试",
                        503,
                        retryable=True,
                    )

            result = SubmissionResult(command.command_id, progress_write.log_id)
            await self._persistence.complete(
                command.command_id,
                result,
                AuditFacts(
                    request_id=request_id,
                    person_id=user.person.person_id,
                    roles=tuple(sorted({role.role for role in user.roles})),
                    task_id=normalized.task_id,
                    file_ids=normalized.file_ids,
                    ip=ip,
                    user_agent=user_agent,
                ),
                now=current_time,
            )
            return result

    def _validate_command(
        self,
        command: CommandSnapshot,
        *,
        user: AuthenticatedUser,
        request: SubmissionInput,
        payload_hash: str,
    ) -> None:
        if (
            command.payload_hash != payload_hash
            or command.person_id != user.person.person_id
            or command.task_id != request.task_id
        ):
            raise SubmissionError("IDEMPOTENCY_CONFLICT", "幂等键已用于其他提交", 409)

    async def _validate_request(
        self,
        request: SubmissionInput,
        *,
        user: AuthenticatedUser,
        task: TaskSnapshot | None,
    ) -> None:
        if task is None:
            raise SubmissionError("TASK_NOT_FOUND", "事项不存在", 404)
        if len(request.content) < 10:
            raise SubmissionError("REPORT_TOO_SHORT", "进展说明至少需要 10 个字符", 422)
        if not 0 <= request.progress <= 100 or request.task_revision < 0:
            raise SubmissionError("REPORT_INVALID", "填报参数无效", 422)
        if any(not FILE_ID_PATTERN.fullmatch(file_id) for file_id in request.file_ids):
            raise SubmissionError("FILE_NOT_CLEAN", "附件状态无效", 422)
        if request.progress < task.progress:
            raise SubmissionError(
                "PROGRESS_REGRESSION_REQUIRES_REVIEW",
                "进度回退需要走更正审核流程",
                409,
            )
        if task.revision not in {request.task_revision, request.task_revision + 1} or (
            task.revision == request.task_revision + 1 and task.progress != request.progress
        ):
            raise SubmissionError(
                "TASK_STALE_REVISION",
                "事项已被他人更新，请刷新后重试",
                409,
            )
        role_names = {role.role for role in user.roles}
        scoped_coordinator = any(
            role.role == "unit_coordinator" and role.scope_unit_id == task.unit_id
            for role in user.roles
        )
        is_admin = "supervision_admin" in role_names
        if request.on_behalf_of is None:
            authorized = task.primary_owner_id == user.person.person_id
        else:
            authorized = request.on_behalf_of == task.primary_owner_id and (
                scoped_coordinator or is_admin
            )
        if not authorized:
            raise SubmissionError("TASK_FORBIDDEN", "无权填报该事项", 403)
        if request.file_ids and not await self._gateway.files_are_clean(
            request.file_ids,
            task_id=task.task_id,
            person_id=user.person.person_id,
        ):
            raise SubmissionError("FILE_NOT_CLEAN", "附件尚未通过安全扫描", 422)


def new_command_id() -> str:
    return str(uuid4())


def _payload_hash(request: SubmissionInput) -> str:
    encoded = json.dumps(asdict(request), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("datetime must be timezone-aware UTC")
    return value.astimezone(UTC)
