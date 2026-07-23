from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from sd_agent.outbox import DispatchOutcome, DispatchResult, OutboxItem
from sd_agent.persistence.models import OutboxAttempt, OutboxMessage


class SqlOutboxRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def claim(
        self,
        *,
        now: datetime,
        batch_size: int,
        lease: timedelta,
    ) -> list[OutboxItem]:
        async with self._sessions.begin() as session:
            rows = list(
                await session.scalars(
                    select(OutboxMessage)
                    .where(
                        OutboxMessage.state.in_(("pending", "retry", "processing")),
                        OutboxMessage.available_at <= now,
                        or_(OutboxMessage.lease_until.is_(None), OutboxMessage.lease_until <= now),
                    )
                    .order_by(OutboxMessage.available_at, OutboxMessage.outbox_id)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            items: list[OutboxItem] = []
            for row in rows:
                row.state = "processing"
                row.lease_until = now + lease
                row.attempt_count += 1
                row.updated_at = now
                items.append(
                    OutboxItem(
                        outbox_id=row.outbox_id,
                        kind=row.kind,
                        dedup_key=row.dedup_key,
                        payload=row.payload,
                        attempt=row.attempt_count,
                        started_at=now,
                    )
                )
        return items

    async def finish(
        self,
        item: OutboxItem,
        result: DispatchResult,
        *,
        outcome: DispatchOutcome,
        available_at: datetime,
        now: datetime,
    ) -> None:
        state = {
            DispatchOutcome.SENT: "sent",
            DispatchOutcome.RETRY: "retry",
            DispatchOutcome.DEAD_LETTER: "dead_letter",
        }[outcome]
        redacted_error = (result.redacted_error or "")[:512] or None
        async with self._sessions.begin() as session:
            updated = await session.scalars(
                update(OutboxMessage)
                .where(
                    OutboxMessage.outbox_id == item.outbox_id,
                    OutboxMessage.state == "processing",
                    OutboxMessage.attempt_count == item.attempt,
                )
                .values(
                    state=state,
                    available_at=available_at,
                    lease_until=None,
                    last_error_code=result.error_code,
                    updated_at=now,
                )
                .returning(OutboxMessage.outbox_id)
            )
            if updated.one_or_none() is None:
                raise RuntimeError("outbox lease was lost")
            session.add(
                OutboxAttempt(
                    attempt_id=str(uuid4()),
                    outbox_id=item.outbox_id,
                    started_at=item.started_at,
                    finished_at=now,
                    result=outcome.value,
                    status_code=result.status_code,
                    redacted_error=redacted_error,
                )
            )
