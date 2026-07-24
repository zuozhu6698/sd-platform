from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from sd_agent.outbox import DispatchResult, OutboxItem
from sd_agent.scheduler.catalog import JOB_SPECS
from sd_agent.scheduler.service import SHANGHAI, SchedulerError, SchedulerService


@dataclass(frozen=True, slots=True)
class SchedulerAdminActor:
    person_id: int
    roles: frozenset[str]
    request_id: str
    ip: str | None
    user_agent: str | None


@dataclass(frozen=True, slots=True)
class JobRunSummary:
    job_run_id: str
    job: str
    scheduled_for: datetime
    state: str
    counts: dict[str, int]
    error_code: str | None
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class JobRunPage:
    items: tuple[JobRunSummary, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class TriggerResult:
    trigger_id: str
    outbox_id: str
    job: str
    scheduled_for: datetime
    retry_of_job_run_id: str | None
    state: str
    idempotent: bool


class SchedulerAdminError(ValueError):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.status_code = status_code


class SchedulerAdminRepository(Protocol):
    async def list_runs(
        self, *, cursor: str | None, limit: int
    ) -> tuple[tuple[JobRunSummary, ...], str | None]: ...

    async def enqueue_trigger(
        self,
        *,
        job: str,
        retry_of_job_run_id: str | None,
        idempotency_key: UUID,
        payload_hash: str,
        actor: SchedulerAdminActor,
        now: datetime,
    ) -> TriggerResult: ...


class SchedulerAdminService:
    def __init__(self, repository: SchedulerAdminRepository) -> None:
        self._repository = repository
        self._jobs = frozenset(spec.name for spec in JOB_SPECS)

    async def list_runs(
        self, *, actor: SchedulerAdminActor, cursor: str | None, limit: int
    ) -> JobRunPage:
        _require_role(actor)
        if not 1 <= limit <= 100:
            raise SchedulerAdminError("JOB_RUN_LIMIT_INVALID", "分页数量无效", 422)
        items, next_cursor = await self._repository.list_runs(cursor=cursor, limit=limit)
        return JobRunPage(items, next_cursor)

    async def trigger(
        self,
        *,
        job: str,
        retry_of_job_run_id: str | None,
        idempotency_key: UUID,
        actor: SchedulerAdminActor,
        now: datetime,
    ) -> TriggerResult:
        _require_role(actor)
        if job not in self._jobs:
            raise SchedulerAdminError("JOB_NOT_FOUND", "计划任务不存在", 404)
        current_time = _require_utc(now)
        payload_hash = _trigger_payload_hash(job, retry_of_job_run_id)
        return await self._repository.enqueue_trigger(
            job=job,
            retry_of_job_run_id=retry_of_job_run_id,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            actor=actor,
            now=current_time,
        )


class SchedulerTriggerOutboxHandler:
    def __init__(self, scheduler: SchedulerService) -> None:
        self._scheduler = scheduler

    async def __call__(self, item: OutboxItem) -> DispatchResult:
        try:
            job = _required_string(item.payload, "job", 64)
            scheduled_for_text = _required_string(item.payload, "scheduled_for", 64)
            parsed = datetime.fromisoformat(scheduled_for_text)
            if parsed.tzinfo is None:
                raise ValueError("scheduled_for must be timezone-aware")
            scheduled_for = parsed.astimezone(SHANGHAI)
            execution = await self._scheduler.run(
                job,
                scheduled_for=scheduled_for,
                now=item.started_at.astimezone(scheduled_for.tzinfo),
            )
        except SchedulerError as exc:
            if exc.code == "JOB_EXECUTION_FAILED":
                # 命令已被消费且失败事实已落 job_run；后续只能由管理员显式重跑。
                return DispatchResult(True, status_code=202)
            return DispatchResult(False, error_code=exc.code, retryable=False)
        except (TypeError, ValueError):
            return DispatchResult(False, error_code="JOB_TRIGGER_INVALID", retryable=False)
        return DispatchResult(
            True,
            status_code=200 if execution.state == "succeeded" else 208,
        )


def _trigger_payload_hash(job: str, retry_of_job_run_id: str | None) -> str:
    canonical = json.dumps(
        {"job": job, "retry_of_job_run_id": retry_of_job_run_id},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("ascii")).hexdigest()


def _required_string(payload: dict[str, object], key: str, max_length: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ValueError(f"invalid {key}")
    return value


def _require_role(actor: SchedulerAdminActor) -> None:
    if actor.roles.isdisjoint({"supervision_admin", "ops_admin"}):
        raise SchedulerAdminError("JOB_RUN_FORBIDDEN", "无权执行该管理操作", 403)


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("datetime must be timezone-aware UTC")
    return value.astimezone(UTC)
