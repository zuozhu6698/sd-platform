from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from sd_agent.outbox import DispatchOutcome, DispatchResult, OutboxItem, OutboxProcessor

NOW = datetime(2026, 7, 23, 6, 0, tzinfo=UTC)


class FakeRepository:
    def __init__(self, items: list[OutboxItem]) -> None:
        self.items = items
        self.finished: list[tuple[OutboxItem, DispatchResult, DispatchOutcome, datetime]] = []
        self.claim_args: tuple[int, timedelta] | None = None

    async def claim(
        self,
        *,
        now: datetime,
        batch_size: int,
        lease: timedelta,
    ) -> list[OutboxItem]:
        self.claim_args = (batch_size, lease)
        return self.items[:batch_size]

    async def finish(
        self,
        item: OutboxItem,
        result: DispatchResult,
        *,
        outcome: DispatchOutcome,
        available_at: datetime,
        now: datetime,
    ) -> None:
        self.finished.append((item, result, outcome, available_at))


class FakeDispatcher:
    def __init__(self, results: list[DispatchResult | Exception]) -> None:
        self.results = results

    async def dispatch(self, item: OutboxItem) -> DispatchResult:
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def item(attempt: int) -> OutboxItem:
    return OutboxItem(
        f"out_{attempt}",
        "oa.complete_pending",
        f"dedup_{attempt}",
        {"task_id": 1},
        attempt,
        NOW,
    )


async def test_processor_records_success_retry_dead_letter_and_exception() -> None:
    repository = FakeRepository([item(1), item(2), item(6), item(1)])
    dispatcher = FakeDispatcher(
        [
            DispatchResult(True, status_code=200),
            DispatchResult(False, status_code=503, error_code="OA_UNAVAILABLE"),
            DispatchResult(False, error_code="OA_REJECTED"),
            RuntimeError("secret network detail"),
        ]
    )
    processor = OutboxProcessor(repository=repository, dispatcher=dispatcher)
    counts = await processor.run_once(now=NOW)
    assert counts == {"claimed": 4, "sent": 1, "retry": 2, "dead_letter": 1}
    assert repository.claim_args == (20, timedelta(seconds=60))
    assert repository.finished[0][2:] == (DispatchOutcome.SENT, NOW)
    assert repository.finished[1][2:] == (
        DispatchOutcome.RETRY,
        NOW + timedelta(seconds=60),
    )
    assert repository.finished[2][2:] == (DispatchOutcome.DEAD_LETTER, NOW)
    exception_result = repository.finished[3][1]
    assert exception_result.error_code == "DISPATCH_EXCEPTION"
    assert exception_result.redacted_error == "RuntimeError"


@pytest.mark.parametrize(
    ("attempt", "delay"),
    [(1, 30), (2, 60), (3, 120), (7, 1920), (8, 3600), (20, 3600)],
)
async def test_retry_backoff_is_deterministic(attempt: int, delay: int) -> None:
    repository = FakeRepository([item(attempt)])
    dispatcher = FakeDispatcher([DispatchResult(False, error_code="TEMP")])
    processor = OutboxProcessor(
        repository=repository,
        dispatcher=dispatcher,
        max_attempts=100,
    )
    await processor.run_once(now=NOW)
    assert repository.finished[0][3] == NOW + timedelta(seconds=delay)


async def test_non_retryable_failure_is_dead_lettered_immediately() -> None:
    repository = FakeRepository([item(1)])
    dispatcher = FakeDispatcher(
        [DispatchResult(False, error_code="INVALID_PAYLOAD", retryable=False)]
    )
    processor = OutboxProcessor(repository=repository, dispatcher=dispatcher)

    counts = await processor.run_once(now=NOW)

    assert counts == {"claimed": 1, "sent": 0, "retry": 0, "dead_letter": 1}
    assert repository.finished[0][2:] == (DispatchOutcome.DEAD_LETTER, NOW)


@pytest.mark.parametrize(
    "kwargs",
    [{"max_attempts": 0}, {"lease_seconds": 4}],
)
def test_processor_rejects_invalid_configuration(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="configuration"):
        OutboxProcessor(
            repository=FakeRepository([]),
            dispatcher=FakeDispatcher([]),
            **kwargs,
        )


@pytest.mark.parametrize("batch_size", [0, 101])
async def test_processor_rejects_invalid_batch(batch_size: int) -> None:
    processor = OutboxProcessor(repository=FakeRepository([]), dispatcher=FakeDispatcher([]))
    with pytest.raises(ValueError, match="batch_size"):
        await processor.run_once(now=NOW, batch_size=batch_size)


@pytest.mark.parametrize(
    "value",
    [NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=8)))],
)
async def test_processor_requires_utc(value: datetime) -> None:
    processor = OutboxProcessor(repository=FakeRepository([]), dispatcher=FakeDispatcher([]))
    with pytest.raises(ValueError, match="UTC"):
        await processor.run_once(now=value)
