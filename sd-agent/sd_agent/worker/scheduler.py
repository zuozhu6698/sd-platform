from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from sd_agent.scheduler.catalog import JOB_SPECS, JobSpec
from sd_agent.scheduler.service import SchedulerError, SchedulerService

SHANGHAI = ZoneInfo("Asia/Shanghai")


class SchedulerAdapter(Protocol):
    def add_job(self, function: object, **values: object) -> object: ...

    def start(self) -> None: ...

    def shutdown(self, *, wait: bool) -> None: ...


class SchedulerLogger(Protocol):
    async def ainfo(self, event: str, **values: object) -> Any: ...

    async def aerror(self, event: str, **values: object) -> Any: ...


class SchedulerWorker:
    def __init__(
        self,
        service: SchedulerService,
        *,
        logger: SchedulerLogger,
        scheduler: SchedulerAdapter | None = None,
        clock: Callable[[], datetime] | None = None,
        specs: tuple[JobSpec, ...] = JOB_SPECS,
    ) -> None:
        self._service = service
        self._logger = logger
        self._scheduler = scheduler or AsyncIOScheduler(timezone=SHANGHAI)
        self._clock = clock or (lambda: datetime.now(SHANGHAI))
        self._specs = {spec.name: spec for spec in specs}
        self._triggers = {
            spec.name: CronTrigger(timezone=SHANGHAI, **dict(spec.cron)) for spec in specs
        }
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        for spec in self._specs.values():
            self._scheduler.add_job(
                self.run_job,
                trigger=self._triggers[spec.name],
                kwargs={"job": spec.name},
                id=f"sd:{spec.name}",
                replace_existing=True,
                coalesce=spec.coalesce,
                max_instances=spec.max_instances,
                misfire_grace_time=spec.misfire_grace_seconds,
            )
        self._scheduler.start()
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        self._scheduler.shutdown(wait=True)
        self._started = False

    async def run_job(self, job: str) -> None:
        trigger = self._triggers.get(job)
        spec = self._specs.get(job)
        if trigger is None or spec is None:
            await self._logger.aerror(
                "scheduled_job_failed", job=job, error_code="JOB_NOT_REGISTERED"
            )
            return
        now = self._clock()
        window_start = now - timedelta(seconds=spec.misfire_grace_seconds)
        scheduled_for = trigger.get_next_fire_time(None, window_start)
        if scheduled_for is None or scheduled_for > now:
            await self._logger.aerror(
                "scheduled_job_failed", job=job, error_code="SCHEDULE_SLOT_UNAVAILABLE"
            )
            return
        try:
            execution = await self._service.run(
                job,
                scheduled_for=scheduled_for,
                now=now,
            )
        except SchedulerError as exc:
            await self._logger.aerror("scheduled_job_failed", job=job, error_code=exc.code)
            return
        await self._logger.ainfo(
            "scheduled_job_completed",
            job=job,
            state=execution.state,
            idempotent=execution.idempotent,
        )
