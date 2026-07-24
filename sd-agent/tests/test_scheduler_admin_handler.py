from __future__ import annotations

from datetime import UTC, datetime

from sd_agent.outbox import OutboxItem
from sd_agent.scheduler.admin import SchedulerTriggerOutboxHandler
from sd_agent.scheduler.service import JobExecution, SchedulerError


class FakeScheduler:
    def __init__(self, error: str | None = None) -> None:
        self.error = error
        self.called: tuple[str, datetime, datetime] | None = None

    async def run(self, job: str, *, scheduled_for: datetime, now: datetime) -> JobExecution:
        self.called = (job, scheduled_for, now)
        if self.error:
            raise SchedulerError(self.error)
        return JobExecution("run-1", job, "succeeded", False)


def item(payload: dict[str, object]) -> OutboxItem:
    return OutboxItem(
        "outbox-1",
        "scheduler.run_job",
        "dedup-1",
        payload,
        1,
        datetime(2026, 7, 24, 4, tzinfo=UTC),
    )


async def test_handler_executes_with_shanghai_time() -> None:
    scheduler = FakeScheduler()
    handler = SchedulerTriggerOutboxHandler(scheduler)  # type: ignore[arg-type]

    result = await handler(item({"job": "urge_scan", "scheduled_for": "2026-07-24T12:00:00+08:00"}))

    assert result.success is True
    assert scheduler.called is not None
    assert getattr(scheduler.called[1].tzinfo, "key", None) == "Asia/Shanghai"
    assert getattr(scheduler.called[2].tzinfo, "key", None) == "Asia/Shanghai"


async def test_handler_treats_recorded_job_failure_as_consumed() -> None:
    handler = SchedulerTriggerOutboxHandler(  # type: ignore[arg-type]
        FakeScheduler("JOB_EXECUTION_FAILED")
    )

    result = await handler(item({"job": "urge_scan", "scheduled_for": "2026-07-24T12:00:00+08:00"}))

    assert result.success is True
    assert result.status_code == 202


async def test_handler_rejects_invalid_or_unregistered_commands() -> None:
    invalid = await SchedulerTriggerOutboxHandler(FakeScheduler())(  # type: ignore[arg-type]
        item({"job": "urge_scan"})
    )
    unknown = await SchedulerTriggerOutboxHandler(  # type: ignore[arg-type]
        FakeScheduler("JOB_NOT_REGISTERED")
    )(item({"job": "unknown", "scheduled_for": "2026-07-24T12:00:00+08:00"}))

    assert invalid.error_code == "JOB_TRIGGER_INVALID"
    assert invalid.retryable is False
    assert unknown.error_code == "JOB_NOT_REGISTERED"
    assert unknown.retryable is False

    naive = await SchedulerTriggerOutboxHandler(FakeScheduler())(  # type: ignore[arg-type]
        item({"job": "urge_scan", "scheduled_for": "2026-07-24T12:00:00"})
    )
    assert naive.error_code == "JOB_TRIGGER_INVALID"
