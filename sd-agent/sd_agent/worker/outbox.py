from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol


class Processor(Protocol):
    async def run_once(self, *, now: datetime, batch_size: int) -> dict[str, int]: ...


class EventLogger(Protocol):
    async def ainfo(self, event: str, **values: object) -> None: ...

    async def aerror(self, event: str, **values: object) -> None: ...


Waiter = Callable[[float], Awaitable[None]]
Clock = Callable[[], datetime]


class OutboxWorker:
    """Drain durable commands without letting a transient batch error stop the process."""

    def __init__(
        self,
        *,
        processor: Processor,
        logger: EventLogger,
        batch_size: int = 20,
        poll_seconds: float = 2.0,
        wait: Waiter | None = None,
        clock: Clock | None = None,
    ) -> None:
        if not 1 <= batch_size <= 100:
            raise ValueError("batch_size must be between 1 and 100")
        if not 0 < poll_seconds <= 60:
            raise ValueError("poll_seconds must be between 0 and 60")
        self._processor = processor
        self._logger = logger
        self._batch_size = batch_size
        self._poll_seconds = poll_seconds
        self._wait = wait
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                counts = await self._processor.run_once(
                    now=self._clock(),
                    batch_size=self._batch_size,
                )
            except Exception as exc:
                await self._logger.aerror(
                    "outbox_batch_failed",
                    error_type=type(exc).__name__,
                )
                await self._wait_for_next_poll(stop)
                continue

            if counts["claimed"]:
                await self._logger.ainfo("outbox_batch_completed", **counts)
                continue
            await self._wait_for_next_poll(stop)

    async def _wait_for_next_poll(self, stop: asyncio.Event) -> None:
        if self._wait is not None:
            await self._wait(self._poll_seconds)
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=self._poll_seconds)
        except TimeoutError:
            return
