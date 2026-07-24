from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from sd_agent.persistence.models import AuditEvent, JobRun, JobTriggerRequest, OutboxMessage
from sd_agent.scheduler.admin import (
    JobRunSummary,
    SchedulerAdminActor,
    SchedulerAdminError,
    TriggerResult,
)
from sd_agent.scheduler.service import SHANGHAI


class SqlSchedulerAdminRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def list_runs(
        self, *, cursor: str | None, limit: int
    ) -> tuple[tuple[JobRunSummary, ...], str | None]:
        statement = select(JobRun).order_by(JobRun.started_at.desc(), JobRun.job_run_id.desc())
        if cursor is not None:
            anchor = select(JobRun.started_at, JobRun.job_run_id).where(JobRun.job_run_id == cursor)
            async with self._sessions() as session:
                anchor_row = (await session.execute(anchor)).one_or_none()
                if anchor_row is None:
                    raise SchedulerAdminError("JOB_RUN_CURSOR_INVALID", "分页游标无效", 422)
                started_at, job_run_id = anchor_row
                statement = statement.where(
                    (JobRun.started_at < started_at)
                    | ((JobRun.started_at == started_at) & (JobRun.job_run_id < job_run_id))
                )
                rows = list(await session.scalars(statement.limit(limit + 1)))
        else:
            async with self._sessions() as session:
                rows = list(await session.scalars(statement.limit(limit + 1)))
        visible = rows[:limit]
        items = tuple(_summary(row) for row in visible)
        next_cursor = visible[-1].job_run_id if len(rows) > limit and visible else None
        return items, next_cursor

    async def enqueue_trigger(
        self,
        *,
        job: str,
        retry_of_job_run_id: str | None,
        idempotency_key: UUID,
        payload_hash: str,
        actor: SchedulerAdminActor,
        now: datetime,
    ) -> TriggerResult:
        key = str(idempotency_key)
        async with self._sessions.begin() as session:
            await session.scalar(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtextextended(f"sd-platform:job-trigger:{key}", 0)
                    )
                )
            )
            existing = await session.scalar(
                select(JobTriggerRequest).where(JobTriggerRequest.idempotency_key == key)
            )
            if existing is not None:
                if existing.payload_hash != payload_hash:
                    raise SchedulerAdminError(
                        "IDEMPOTENCY_CONFLICT", "幂等键已用于其他触发请求", 409
                    )
                return _trigger_result(existing, idempotent=True)

            if retry_of_job_run_id is not None:
                failed_run = await session.scalar(
                    select(JobRun).where(JobRun.job_run_id == retry_of_job_run_id).with_for_update()
                )
                if failed_run is None:
                    raise SchedulerAdminError("JOB_RUN_NOT_FOUND", "待重跑记录不存在", 404)
                if failed_run.job != job or failed_run.state != "failed":
                    raise SchedulerAdminError(
                        "JOB_RUN_NOT_RETRYABLE", "仅允许重跑同任务的失败记录", 409
                    )
                previous_retry = await session.scalar(
                    select(JobTriggerRequest).where(
                        JobTriggerRequest.retry_of_job_run_id == retry_of_job_run_id
                    )
                )
                if previous_retry is not None:
                    raise SchedulerAdminError(
                        "JOB_RUN_ALREADY_RETRIED", "该失败记录已有重跑请求", 409
                    )

            trigger_id = str(uuid4())
            outbox_id = str(uuid4())
            scheduled_for = now.astimezone(SHANGHAI)
            row = JobTriggerRequest(
                trigger_id=trigger_id,
                idempotency_key=key,
                payload_hash=payload_hash,
                job=job,
                scheduled_for=scheduled_for,
                retry_of_job_run_id=retry_of_job_run_id,
                requested_by=actor.person_id,
                outbox_id=outbox_id,
                created_at=now,
            )
            session.add(row)
            session.add(
                OutboxMessage(
                    outbox_id=outbox_id,
                    kind="scheduler.run_job",
                    dedup_key=f"scheduler-trigger:{trigger_id}",
                    payload={
                        "trigger_id": trigger_id,
                        "job": job,
                        "scheduled_for": scheduled_for.isoformat(),
                        "retry_of_job_run_id": retry_of_job_run_id,
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
            session.add(_audit(actor, row, now))
            return _trigger_result(row, idempotent=False)


def _summary(row: JobRun) -> JobRunSummary:
    return JobRunSummary(
        row.job_run_id,
        row.job,
        row.scheduled_for,
        row.state,
        dict(row.counts),
        row.error_code,
        row.started_at,
        row.finished_at,
    )


def _trigger_result(row: JobTriggerRequest, *, idempotent: bool) -> TriggerResult:
    return TriggerResult(
        row.trigger_id,
        row.outbox_id,
        row.job,
        row.scheduled_for,
        row.retry_of_job_run_id,
        "queued",
        idempotent,
    )


def _audit(actor: SchedulerAdminActor, row: JobTriggerRequest, now: datetime) -> AuditEvent:
    return AuditEvent(
        event_id=str(uuid4()),
        request_id=actor.request_id,
        who=str(actor.person_id),
        role=",".join(sorted(actor.roles))[:64] or None,
        scope={},
        what="scheduler.job.trigger",
        target_type="job_trigger_request",
        target_id=row.trigger_id,
        result="queued",
        ip=actor.ip,
        user_agent=actor.user_agent,
        details={"job": row.job, "retry_of_job_run_id": row.retry_of_job_run_id},
        created_at=now,
    )
