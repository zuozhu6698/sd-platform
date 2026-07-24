from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from sd_agent.persistence.models import JobRun
from sd_agent.scheduler.service import JobRunClaim


class SqlJobRunRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def claim_run(
        self,
        *,
        job: str,
        scheduled_for: datetime,
        config_hash: str,
        started_at: datetime,
    ) -> JobRunClaim | None:
        lock_key = f"sd-platform:job:{job}:{scheduled_for.isoformat()}"
        async with self._sessions.begin() as session:
            locked = await session.scalar(
                select(func.pg_try_advisory_xact_lock(func.hashtextextended(lock_key, 0)))
            )
            if not locked:
                return None
            result = await session.execute(
                insert(JobRun)
                .values(
                    job_run_id=str(uuid4()),
                    job=job,
                    scheduled_for=scheduled_for,
                    state="running",
                    config_hash=config_hash,
                    counts={},
                    error_code=None,
                    started_at=started_at,
                    finished_at=None,
                )
                .on_conflict_do_nothing(index_elements=["job", "scheduled_for"])
                .returning(JobRun.job_run_id)
            )
            job_run_id = result.scalar_one_or_none()
            return JobRunClaim(job_run_id) if job_run_id is not None else None

    async def finish_run(
        self,
        *,
        job_run_id: str,
        state: str,
        counts: Mapping[str, int],
        error_code: str | None,
        finished_at: datetime,
    ) -> None:
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(JobRun)
                .where(JobRun.job_run_id == job_run_id, JobRun.state == "running")
                .values(
                    state=state,
                    counts=dict(counts),
                    error_code=error_code,
                    finished_at=finished_at,
                )
                .returning(JobRun.job_run_id)
            )
            if result.scalar_one_or_none() is None:
                raise RuntimeError("job run claim was lost")
            return None
