from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from sd_agent.scheduler.service import JobExecution, SchedulerError
from sd_agent.worker import scheduler as scheduler_module
from sd_agent.worker.scheduler import SchedulerWorker

SHANGHAI = ZoneInfo("Asia/Shanghai")


class FakeService:
    def __init__(self, error: SchedulerError | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, datetime, datetime]] = []

    async def run(
        self,
        job: str,
        *,
        scheduled_for: datetime,
        now: datetime,
    ) -> JobExecution:
        self.calls.append((job, scheduled_for, now))
        if self.error is not None:
            raise self.error
        return JobExecution("run-1", job, "succeeded", False)


class FakeScheduler:
    def __init__(self) -> None:
        self.jobs: list[dict[str, object]] = []
        self.started = False
        self.shutdown_wait: bool | None = None

    def add_job(self, function: object, **values: object) -> None:
        self.jobs.append({"function": function, **values})

    def start(self) -> None:
        self.started = True

    def shutdown(self, *, wait: bool) -> None:
        self.shutdown_wait = wait


class FakeLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    async def ainfo(self, event: str, **values: object) -> None:
        self.events.append((event, values))

    async def aerror(self, event: str, **values: object) -> None:
        self.events.append((event, values))


async def test_worker_registers_all_jobs_and_stops_cleanly() -> None:
    scheduler = FakeScheduler()
    worker = SchedulerWorker(FakeService(), logger=FakeLogger(), scheduler=scheduler)

    await worker.start()
    await worker.start()

    assert scheduler.started is True
    assert len(scheduler.jobs) == 7
    assert {str(job["id"]) for job in scheduler.jobs} == {
        "sd:urge_scan",
        "sd:report_reminder",
        "sd:ai_review",
        "sd:weekly_report",
        "sd:monthly_report",
        "sd:reconciliation",
        "sd:weekly_snapshot",
    }
    assert all(job["replace_existing"] is True for job in scheduler.jobs)

    await worker.stop()
    assert scheduler.shutdown_wait is True


async def test_worker_derives_the_scheduled_slot_in_shanghai_time() -> None:
    service = FakeService()
    logger = FakeLogger()
    now = datetime(2026, 7, 24, 9, 3, tzinfo=SHANGHAI)
    worker = SchedulerWorker(
        service,
        logger=logger,
        scheduler=FakeScheduler(),
        clock=lambda: now,
    )

    await worker.run_job("urge_scan")

    assert service.calls == [("urge_scan", now.replace(minute=0), now)]
    assert logger.events == [
        (
            "scheduled_job_completed",
            {"job": "urge_scan", "state": "succeeded", "idempotent": False},
        )
    ]


async def test_worker_contains_scheduler_errors_without_logging_exception_text() -> None:
    logger = FakeLogger()
    worker = SchedulerWorker(
        FakeService(SchedulerError("JOB_EXECUTION_FAILED")),
        logger=logger,
        scheduler=FakeScheduler(),
        clock=lambda: datetime(2026, 7, 24, 9, 0, tzinfo=SHANGHAI),
    )

    await worker.run_job("urge_scan")

    assert logger.events == [
        ("scheduled_job_failed", {"job": "urge_scan", "error_code": "JOB_EXECUTION_FAILED"})
    ]


async def test_worker_stop_is_safe_before_start() -> None:
    scheduler = FakeScheduler()
    worker = SchedulerWorker(FakeService(), logger=FakeLogger(), scheduler=scheduler)
    await worker.stop()
    assert scheduler.shutdown_wait is None


async def test_worker_rejects_unknown_job_and_outside_misfire_window() -> None:
    logger = FakeLogger()
    worker = SchedulerWorker(
        FakeService(),
        logger=logger,
        scheduler=FakeScheduler(),
        clock=lambda: datetime(2026, 7, 24, 9, 20, tzinfo=SHANGHAI),
    )
    await worker.run_job("unknown")
    await worker.run_job("urge_scan")
    assert logger.events == [
        ("scheduled_job_failed", {"job": "unknown", "error_code": "JOB_NOT_REGISTERED"}),
        (
            "scheduled_job_failed",
            {"job": "urge_scan", "error_code": "SCHEDULE_SLOT_UNAVAILABLE"},
        ),
    ]


def test_worker_can_build_the_real_adapter_and_default_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeScheduler()
    monkeypatch.setattr(scheduler_module, "AsyncIOScheduler", lambda **_values: fake)
    worker = SchedulerWorker(FakeService(), logger=FakeLogger())
    assert worker._scheduler is fake
    assert getattr(worker._clock().tzinfo, "key", None) == "Asia/Shanghai"
