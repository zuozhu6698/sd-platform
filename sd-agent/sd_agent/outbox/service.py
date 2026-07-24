from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol


class DispatchOutcome(StrEnum):
    SENT = "sent"
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True, slots=True)
class OutboxItem:
    outbox_id: str
    kind: str
    dedup_key: str
    payload: dict[str, Any]
    attempt: int
    started_at: datetime


@dataclass(frozen=True, slots=True)
class DispatchResult:
    success: bool
    status_code: int | None = None
    error_code: str | None = None
    redacted_error: str | None = None
    retryable: bool = True


class OutboxRepository(Protocol):
    async def claim(
        self,
        *,
        now: datetime,
        batch_size: int,
        lease: timedelta,
    ) -> list[OutboxItem]: ...

    async def finish(
        self,
        item: OutboxItem,
        result: DispatchResult,
        *,
        outcome: DispatchOutcome,
        available_at: datetime,
        now: datetime,
    ) -> None: ...


class OutboxDispatcher(Protocol):
    async def dispatch(self, item: OutboxItem) -> DispatchResult: ...


OutboxHandler = Callable[[OutboxItem], Awaitable[DispatchResult]]


class HandlerOutboxDispatcher:
    """Route durable commands only to explicitly registered handlers."""

    def __init__(self, handlers: Mapping[str, OutboxHandler]) -> None:
        self._handlers = dict(handlers)

    async def dispatch(self, item: OutboxItem) -> DispatchResult:
        handler = self._handlers.get(item.kind)
        if handler is None:
            return DispatchResult(
                success=False,
                error_code="OUTBOX_KIND_UNSUPPORTED",
                redacted_error=item.kind[:128],
                retryable=False,
            )
        return await handler(item)


class OutboxProcessor:
    def __init__(
        self,
        *,
        repository: OutboxRepository,
        dispatcher: OutboxDispatcher,
        max_attempts: int = 6,
        lease_seconds: int = 60,
    ) -> None:
        if max_attempts < 1 or lease_seconds < 5:
            raise ValueError("invalid outbox retry configuration")
        self._repository = repository
        self._dispatcher = dispatcher
        self._max_attempts = max_attempts
        self._lease = timedelta(seconds=lease_seconds)

    async def run_once(self, *, now: datetime, batch_size: int = 20) -> dict[str, int]:
        current_time = _require_utc(now)
        if not 1 <= batch_size <= 100:
            raise ValueError("batch_size must be between 1 and 100")
        items = await self._repository.claim(
            now=current_time,
            batch_size=batch_size,
            lease=self._lease,
        )
        counts = {"claimed": len(items), "sent": 0, "retry": 0, "dead_letter": 0}
        for item in items:
            try:
                result = await self._dispatcher.dispatch(item)
            except Exception as exc:
                result = DispatchResult(
                    False,
                    error_code="DISPATCH_EXCEPTION",
                    redacted_error=type(exc).__name__,
                )
            outcome, available_at = self._outcome(item, result, now=current_time)
            await self._repository.finish(
                item,
                result,
                outcome=outcome,
                available_at=available_at,
                now=current_time,
            )
            counts[outcome.value] += 1
        return counts

    def _outcome(
        self,
        item: OutboxItem,
        result: DispatchResult,
        *,
        now: datetime,
    ) -> tuple[DispatchOutcome, datetime]:
        if result.success:
            return DispatchOutcome.SENT, now
        if not result.retryable:
            return DispatchOutcome.DEAD_LETTER, now
        if item.attempt >= self._max_attempts:
            return DispatchOutcome.DEAD_LETTER, now
        delay_seconds = min(30 * (2 ** (item.attempt - 1)), 3600)
        return DispatchOutcome.RETRY, now + timedelta(seconds=delay_seconds)


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("datetime must be timezone-aware UTC")
    return value.astimezone(UTC)
