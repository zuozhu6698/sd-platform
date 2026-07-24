from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from sd_agent.adapters.submission import TeableSubmissionGateway
from sd_agent.adapters.teable import TeableAdapterError, TeableClient, TeableFilter
from sd_agent.persistence.models import AuditEvent, FileObject, OutboxMessage, SubmissionCommand
from sd_agent.scheduler.service import JobOutcome
from sd_agent.submission import CommandState, SubmissionResult, TaskSnapshot
from sd_agent.submission.service import FILE_ID_PATTERN


@dataclass(frozen=True, slots=True)
class ReconciliationCandidate:
    command_id: str
    person_id: int
    task_id: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ProgressRecovery:
    record_id: str
    log_id: int
    command_id: str
    task_id: int
    reporter_id: int
    progress: int
    file_ids: tuple[str, ...]


class ReconciliationClaim(Protocol):
    candidate: ReconciliationCandidate

    async def complete(self, recovery: ProgressRecovery, *, now: datetime) -> None: ...


class ReconciliationRepository(Protocol):
    async def list_candidates(
        self, *, before: datetime, limit: int
    ) -> tuple[ReconciliationCandidate, ...]: ...

    def claim(
        self, candidate: ReconciliationCandidate, *, before: datetime
    ) -> AbstractAsyncContextManager[ReconciliationClaim | None]: ...


class ReconciliationGateway(Protocol):
    async def find_recovery(self, command_id: str) -> ProgressRecovery | None: ...

    async def get_task(self, task_id: int) -> TaskSnapshot | None: ...

    async def update_task_progress(
        self,
        task: TaskSnapshot,
        *,
        progress: int,
        next_revision: int,
        idempotency_key: str,
    ) -> TaskSnapshot: ...


class ReconciliationHandler:
    def __init__(
        self,
        *,
        repository: ReconciliationRepository,
        gateway: ReconciliationGateway,
        stale_after: timedelta = timedelta(minutes=5),
        batch_size: int = 100,
    ) -> None:
        if stale_after < timedelta(minutes=1) or not 1 <= batch_size <= 500:
            raise ValueError("invalid reconciliation configuration")
        self._repository = repository
        self._gateway = gateway
        self._stale_after = stale_after
        self._batch_size = batch_size

    async def __call__(self, scheduled_for: datetime) -> JobOutcome:
        before = scheduled_for - self._stale_after
        candidates = await self._repository.list_candidates(
            before=before,
            limit=self._batch_size,
        )
        counts = {
            "candidates": len(candidates),
            "locked": 0,
            "missing_progress": 0,
            "already_current": 0,
            "task_updated": 0,
            "recovered": 0,
        }
        for candidate in candidates:
            async with self._repository.claim(candidate, before=before) as claim:
                if claim is None:
                    counts["locked"] += 1
                    continue
                recovery = await self._gateway.find_recovery(candidate.command_id)
                if recovery is None:
                    counts["missing_progress"] += 1
                    continue
                _validate_recovery(candidate, recovery)
                task = await self._gateway.get_task(candidate.task_id)
                if task is None:
                    raise RuntimeError("RECONCILIATION_TASK_MISSING")
                if task.progress < recovery.progress:
                    updated = await self._gateway.update_task_progress(
                        task,
                        progress=recovery.progress,
                        next_revision=task.revision + 1,
                        idempotency_key=f"reconcile:{candidate.command_id}",
                    )
                    if (
                        updated.progress != recovery.progress
                        or updated.revision != task.revision + 1
                    ):
                        raise RuntimeError("RECONCILIATION_WRITE_UNCONFIRMED")
                    counts["task_updated"] += 1
                else:
                    counts["already_current"] += 1
                await claim.complete(recovery, now=scheduled_for)
                counts["recovered"] += 1
        return JobOutcome(counts)


class _RecoveryFields(BaseModel):
    model_config = ConfigDict(extra="ignore")

    log_id: int = Field(gt=0)
    command_id: str
    task_id: int = Field(gt=0)
    reporter_id: int = Field(gt=0)
    progress: int = Field(ge=0, le=100)
    attachments: list[str] = Field(default_factory=list)


class TeableReconciliationGateway:
    def __init__(self, *, teable: TeableClient, submission: TeableSubmissionGateway) -> None:
        self._teable = teable
        self._submission = submission

    async def find_recovery(self, command_id: str) -> ProgressRecovery | None:
        records = await self._teable.list_records(
            "progress_log",
            projection=(
                "log_id",
                "command_id",
                "task_id",
                "reporter_id",
                "progress",
                "attachments",
            ),
            filter_by=TeableFilter("command_id", "is", command_id),
            take=2,
        )
        if not records:
            return None
        if len(records) != 1:
            raise TeableAdapterError("TEABLE_PROGRESS_CONFLICT", retryable=False)
        try:
            fields = _RecoveryFields.model_validate(records[0].fields)
        except ValidationError as exc:
            raise TeableAdapterError("TEABLE_INVALID_RESPONSE", retryable=False) from exc
        if fields.command_id != command_id or any(
            FILE_ID_PATTERN.fullmatch(value) is None for value in fields.attachments
        ):
            raise TeableAdapterError("TEABLE_INVALID_RESPONSE", retryable=False)
        return ProgressRecovery(
            records[0].id,
            fields.log_id,
            fields.command_id,
            fields.task_id,
            fields.reporter_id,
            fields.progress,
            tuple(sorted(set(fields.attachments))),
        )

    async def get_task(self, task_id: int) -> TaskSnapshot | None:
        return await self._submission.get_task(task_id)

    async def update_task_progress(
        self,
        task: TaskSnapshot,
        *,
        progress: int,
        next_revision: int,
        idempotency_key: str,
    ) -> TaskSnapshot:
        return await self._submission.update_task_progress(
            task,
            progress=progress,
            next_revision=next_revision,
            idempotency_key=idempotency_key,
        )


class SqlReconciliationRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def list_candidates(
        self, *, before: datetime, limit: int
    ) -> tuple[ReconciliationCandidate, ...]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(SubmissionCommand)
                .where(
                    SubmissionCommand.state.in_(
                        (CommandState.PENDING.value, CommandState.TEABLE_WRITTEN.value)
                    ),
                    SubmissionCommand.updated_at <= before,
                )
                .order_by(SubmissionCommand.updated_at, SubmissionCommand.command_id)
                .limit(limit)
            )
            return tuple(_candidate(row) for row in rows)

    @asynccontextmanager
    async def claim(
        self, candidate: ReconciliationCandidate, *, before: datetime
    ) -> AsyncIterator[ReconciliationClaim | None]:
        async with self._sessions.begin() as session:
            lock = func.pg_try_advisory_xact_lock(func.hashtextextended(candidate.command_id, 0))
            locked = await session.scalar(select(lock))
            if locked is not True:
                yield None
                return
            row = await session.scalar(
                select(SubmissionCommand)
                .where(
                    SubmissionCommand.command_id == candidate.command_id,
                    SubmissionCommand.state.in_(
                        (CommandState.PENDING.value, CommandState.TEABLE_WRITTEN.value)
                    ),
                    SubmissionCommand.updated_at <= before,
                )
                .with_for_update()
            )
            if row is None:
                yield None
                return
            yield _SqlReconciliationClaim(_candidate(row), session, row)


class _SqlReconciliationClaim:
    def __init__(
        self,
        candidate: ReconciliationCandidate,
        session: AsyncSession,
        row: SubmissionCommand,
    ) -> None:
        self.candidate = candidate
        self._session = session
        self._row = row

    async def complete(self, recovery: ProgressRecovery, *, now: datetime) -> None:
        if recovery.file_ids:
            rows = await self._session.scalars(
                update(FileObject)
                .where(
                    FileObject.file_id.in_(recovery.file_ids),
                    FileObject.owner_person_id == self.candidate.person_id,
                    FileObject.state == "clean",
                    (FileObject.task_id.is_(None) | (FileObject.task_id == self.candidate.task_id)),
                )
                .values(task_id=self.candidate.task_id, bound_at=now)
                .returning(FileObject.file_id)
            )
            if set(rows) != set(recovery.file_ids):
                raise RuntimeError("RECONCILIATION_FILE_BINDING_CHANGED")
        result = SubmissionResult(self.candidate.command_id, recovery.log_id)
        self._row.state = CommandState.COMMITTED.value
        self._row.teable_record_id = recovery.record_id
        self._row.result = {
            "submission_id": result.submission_id,
            "log_id": result.log_id,
            "state": result.state,
        }
        self._row.last_error_code = None
        self._row.updated_at = now
        self._session.add(
            AuditEvent(
                event_id=str(uuid4()),
                request_id=f"reconcile:{self.candidate.command_id}"[:64],
                who=str(self.candidate.person_id),
                role=None,
                scope={},
                what="report.reconcile",
                target_type="task",
                target_id=str(self.candidate.task_id),
                result="success",
                ip=None,
                user_agent=None,
                details={"command_id": self.candidate.command_id, "log_id": recovery.log_id},
                created_at=now,
            )
        )
        self._session.add(
            OutboxMessage(
                outbox_id=str(uuid4()),
                kind="oa.complete_pending",
                dedup_key=f"submission:{self.candidate.command_id}:oa.complete_pending",
                payload={
                    "command_id": self.candidate.command_id,
                    "task_id": self.candidate.task_id,
                    "person_id": self.candidate.person_id,
                    "log_id": recovery.log_id,
                },
                state="pending",
                available_at=now,
                lease_until=None,
                attempt_count=0,
                last_error_code=None,
                created_at=now,
                updated_at=now,
            )
        )


def _candidate(row: Any) -> ReconciliationCandidate:
    return ReconciliationCandidate(row.command_id, row.person_id, row.task_id, row.updated_at)


def _validate_recovery(
    candidate: ReconciliationCandidate,
    recovery: ProgressRecovery,
) -> None:
    if (
        recovery.command_id != candidate.command_id
        or recovery.task_id != candidate.task_id
        or recovery.reporter_id != candidate.person_id
    ):
        raise RuntimeError("RECONCILIATION_IDENTITY_MISMATCH")
