from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from sd_agent.scheduler.service import (
    JobExecution,
    JobOutcome,
    JobRunClaim,
    SchedulerError,
    SchedulerService,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
SCHEDULED = datetime(2026, 7, 24, 9, 0, tzinfo=SHANGHAI)


class FakeRepository:
    def __init__(self, claim: JobRunClaim | None = None) -> None:
        self.claim = claim
        self.claim_calls: list[dict[str, object]] = []
        self.finish_calls: list[dict[str, object]] = []

    async def claim_run(self, **values: object) -> JobRunClaim | None:
        self.claim_calls.append(values)
        return self.claim

    async def finish_run(self, **values: object) -> None:
        self.finish_calls.append(values)


class SuccessfulHandler:
    async def __call__(self, scheduled_for: datetime) -> JobOutcome:
        assert scheduled_for == SCHEDULED
        return JobOutcome({"scanned": 4, "queued": 2})


class FailedHandler:
    async def __call__(self, _scheduled_for: datetime) -> JobOutcome:
        raise RuntimeError("secret external response")


class InvalidCountsHandler:
    async def __call__(self, _scheduled_for: datetime) -> JobOutcome:
        return JobOutcome({"queued": -1})


async def test_scheduler_claims_runs_and_persists_success() -> None:
    repository = FakeRepository(JobRunClaim("run-1"))
    service = SchedulerService(
        repository,
        handlers={"urge_scan": SuccessfulHandler()},
        config_hash="a" * 64,
        clock=lambda: SCHEDULED,
    )

    result = await service.run("urge_scan", scheduled_for=SCHEDULED, now=SCHEDULED)

    assert result == JobExecution("run-1", "urge_scan", "succeeded", False)
    assert repository.claim_calls == [
        {
            "job": "urge_scan",
            "scheduled_for": SCHEDULED,
            "config_hash": "a" * 64,
            "started_at": SCHEDULED,
        }
    ]
    assert repository.finish_calls == [
        {
            "job_run_id": "run-1",
            "state": "succeeded",
            "counts": {"scanned": 4, "queued": 2},
            "error_code": None,
            "finished_at": SCHEDULED,
        }
    ]


async def test_duplicate_schedule_is_an_idempotent_skip() -> None:
    repository = FakeRepository()
    service = SchedulerService(
        repository,
        handlers={"urge_scan": SuccessfulHandler()},
        config_hash="b" * 64,
        clock=lambda: SCHEDULED,
    )

    result = await service.run("urge_scan", scheduled_for=SCHEDULED, now=SCHEDULED)

    assert result == JobExecution(None, "urge_scan", "duplicate", True)
    assert repository.finish_calls == []


async def test_handler_failure_is_redacted_and_persisted() -> None:
    repository = FakeRepository(JobRunClaim("run-2"))
    service = SchedulerService(
        repository,
        handlers={"urge_scan": FailedHandler()},
        config_hash="c" * 64,
        clock=lambda: SCHEDULED,
    )

    with pytest.raises(SchedulerError, match="JOB_EXECUTION_FAILED"):
        await service.run("urge_scan", scheduled_for=SCHEDULED, now=SCHEDULED)

    assert repository.finish_calls == [
        {
            "job_run_id": "run-2",
            "state": "failed",
            "counts": {},
            "error_code": "JOB_EXECUTION_FAILED",
            "finished_at": SCHEDULED,
        }
    ]
    assert "secret external response" not in str(repository.finish_calls)


async def test_invalid_handler_counts_fail_closed() -> None:
    repository = FakeRepository(JobRunClaim("run-3"))
    service = SchedulerService(
        repository,
        handlers={"urge_scan": InvalidCountsHandler()},
        config_hash="e" * 64,
        clock=lambda: SCHEDULED,
    )
    with pytest.raises(SchedulerError, match="JOB_EXECUTION_FAILED"):
        await service.run("urge_scan", scheduled_for=SCHEDULED, now=SCHEDULED)
    assert repository.finish_calls[0]["state"] == "failed"


def test_default_clock_is_shanghai_aware() -> None:
    service = SchedulerService(
        FakeRepository(),
        handlers={"urge_scan": SuccessfulHandler()},
        config_hash="f" * 64,
    )
    assert getattr(service._clock().tzinfo, "key", None) == "Asia/Shanghai"


@pytest.mark.parametrize(
    ("job", "scheduled_for", "config_hash", "message"),
    [
        ("missing", SCHEDULED, "d" * 64, "JOB_NOT_REGISTERED"),
        ("urge_scan", SCHEDULED.replace(tzinfo=None), "d" * 64, "SCHEDULE_TIME_INVALID"),
        ("urge_scan", SCHEDULED, "bad", "CONFIG_HASH_INVALID"),
    ],
)
async def test_scheduler_rejects_invalid_contracts(
    job: str,
    scheduled_for: datetime,
    config_hash: str,
    message: str,
) -> None:
    service = SchedulerService(
        FakeRepository(),
        handlers={"urge_scan": SuccessfulHandler()},
        config_hash=config_hash,
        clock=lambda: SCHEDULED,
    )
    with pytest.raises(SchedulerError, match=message):
        await service.run(job, scheduled_for=scheduled_for, now=SCHEDULED)
