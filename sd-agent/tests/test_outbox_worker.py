from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sd_agent.outbox import DispatchResult, HandlerOutboxDispatcher, OutboxItem
from sd_agent.worker.outbox import OutboxWorker


def item(kind: str = "oa.complete_pending") -> OutboxItem:
    return OutboxItem(
        outbox_id="out-1",
        kind=kind,
        dedup_key="submission:1:oa.complete_pending",
        payload={"task_id": 1},
        attempt=1,
        started_at=datetime(2026, 7, 24, tzinfo=UTC),
    )


async def test_dispatcher_routes_registered_kind() -> None:
    seen: list[OutboxItem] = []

    async def handler(message: OutboxItem) -> DispatchResult:
        seen.append(message)
        return DispatchResult(success=True, status_code=202)

    dispatcher = HandlerOutboxDispatcher({"oa.complete_pending": handler})

    result = await dispatcher.dispatch(item())

    assert result == DispatchResult(success=True, status_code=202)
    assert seen == [item()]


async def test_dispatcher_rejects_unknown_kind_without_retry() -> None:
    dispatcher = HandlerOutboxDispatcher({})

    result = await dispatcher.dispatch(item("unknown.action"))

    assert result.success is False
    assert result.retryable is False
    assert result.error_code == "OUTBOX_KIND_UNSUPPORTED"
    assert result.redacted_error == "unknown.action"


class FakeProcessor:
    def __init__(self, results: list[dict[str, int] | Exception]) -> None:
        self.results = results
        self.calls: list[tuple[datetime, int]] = []

    async def run_once(self, *, now: datetime, batch_size: int) -> dict[str, int]:
        self.calls.append((now, batch_size))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    async def ainfo(self, event: str, **values: object) -> None:
        self.events.append((event, values))

    async def aerror(self, event: str, **values: object) -> None:
        self.events.append((event, values))


async def test_worker_drains_batches_and_stops() -> None:
    stop = asyncio.Event()
    processor = FakeProcessor(
        [
            {"claimed": 2, "sent": 2, "retry": 0, "dead_letter": 0},
            {"claimed": 0, "sent": 0, "retry": 0, "dead_letter": 0},
        ]
    )
    logger = FakeLogger()

    async def wait_for_stop(_seconds: float) -> None:
        stop.set()

    worker = OutboxWorker(
        processor=processor,
        logger=logger,
        batch_size=10,
        poll_seconds=0.1,
        wait=wait_for_stop,
        clock=lambda: datetime(2026, 7, 24, tzinfo=UTC),
    )

    await worker.run(stop)

    assert len(processor.calls) == 2
    assert all(batch_size == 10 for _, batch_size in processor.calls)
    assert logger.events[0] == (
        "outbox_batch_completed",
        {"claimed": 2, "sent": 2, "retry": 0, "dead_letter": 0},
    )


async def test_worker_contains_repository_failure_and_retries_after_delay() -> None:
    stop = asyncio.Event()
    processor = FakeProcessor([RuntimeError("database DSN must not be logged")])
    logger = FakeLogger()

    async def wait_for_stop(_seconds: float) -> None:
        stop.set()

    worker = OutboxWorker(
        processor=processor,
        logger=logger,
        wait=wait_for_stop,
        clock=lambda: datetime(2026, 7, 24, tzinfo=UTC),
    )

    await worker.run(stop)

    assert logger.events == [
        ("outbox_batch_failed", {"error_type": "RuntimeError"})
    ]


async def test_worker_wait_is_interruptible_by_stop_event() -> None:
    stop = asyncio.Event()
    processor = FakeProcessor(
        [{"claimed": 0, "sent": 0, "retry": 0, "dead_letter": 0}]
    )
    waits: list[float] = []

    async def wait_for_stop(seconds: float) -> None:
        waits.append(seconds)
        stop.set()

    worker = OutboxWorker(
        processor=processor,
        logger=FakeLogger(),
        poll_seconds=3.0,
        wait=wait_for_stop,
        clock=lambda: datetime(2026, 7, 24, tzinfo=UTC),
    )

    await worker.run(stop)

    assert waits == [3.0]


async def test_default_wait_handles_timeout_and_stop() -> None:
    worker = OutboxWorker(
        processor=FakeProcessor([]),
        logger=FakeLogger(),
        poll_seconds=0.001,
    )
    stop = asyncio.Event()

    await worker._wait_for_next_poll(stop)
    stop.set()
    await worker._wait_for_next_poll(stop)


def test_worker_rejects_invalid_limits() -> None:
    processor = FakeProcessor([])
    logger = FakeLogger()
    base: dict[str, object] = {"processor": processor, "logger": logger}
    invalid: list[dict[str, int | float]] = [
        {"batch_size": 0},
        {"batch_size": 101},
        {"poll_seconds": 0},
        {"poll_seconds": 61},
    ]

    for values in invalid:
        try:
            OutboxWorker(**base, **values)  # type: ignore[arg-type]
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {values}")
