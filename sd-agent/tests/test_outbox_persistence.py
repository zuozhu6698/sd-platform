from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from sd_agent.outbox import DispatchOutcome, DispatchResult, OutboxItem
from sd_agent.persistence import outbox as outbox_module
from sd_agent.persistence.models import OutboxAttempt, OutboxMessage
from sd_agent.persistence.outbox import SqlOutboxRepository

NOW = datetime(2026, 7, 24, 3, 0, tzinfo=UTC)


def message(outbox_id: str = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa") -> OutboxMessage:
    return OutboxMessage(
        outbox_id=outbox_id,
        kind="oa.complete_pending",
        dedup_key=f"submission:{outbox_id}",
        payload={"task_id": 101},
        state="pending",
        available_at=NOW,
        lease_until=None,
        attempt_count=0,
        last_error_code=None,
        created_at=NOW,
        updated_at=NOW,
    )


class FakeResult:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def one_or_none(self) -> str | None:
        return self.value


class FakeSession:
    def __init__(
        self,
        *,
        claim_rows: list[OutboxMessage] | None = None,
        updated_id: str | None = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    ) -> None:
        self.claim_rows = claim_rows
        self.updated_id = updated_id
        self.added: list[object] = []
        self.calls = 0

    async def scalars(self, _statement: object) -> object:
        self.calls += 1
        if self.calls == 1 and self.claim_rows is not None:
            return self.claim_rows
        return FakeResult(self.updated_id)

    def add(self, value: object) -> None:
        self.added.append(value)


class FakeSessions:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeSession]:
        yield self.session


def repository(session: FakeSession) -> SqlOutboxRepository:
    instance = object.__new__(SqlOutboxRepository)
    instance._sessions = FakeSessions(session)  # type: ignore[assignment]
    return instance


def test_repository_builds_non_expiring_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[object, bool]] = []

    def factory(engine: object, *, expire_on_commit: bool) -> object:
        seen.append((engine, expire_on_commit))
        return object()

    monkeypatch.setattr(outbox_module, "async_sessionmaker", factory)
    engine = object()
    SqlOutboxRepository(engine)  # type: ignore[arg-type]
    assert seen == [(engine, False)]


async def test_claim_leases_rows_and_increments_attempt() -> None:
    row = message()

    items = await repository(FakeSession(claim_rows=[row])).claim(
        now=NOW,
        batch_size=20,
        lease=timedelta(seconds=60),
    )

    assert items == [
        OutboxItem(
            row.outbox_id,
            row.kind,
            row.dedup_key,
            row.payload,
            1,
            NOW,
        )
    ]
    assert row.state == "processing"
    assert row.lease_until == NOW + timedelta(seconds=60)
    assert row.attempt_count == 1


async def test_claim_empty_batch() -> None:
    session = FakeSession(claim_rows=[])
    items = await repository(session).claim(
        now=NOW,
        batch_size=20,
        lease=timedelta(seconds=60),
    )
    assert items == []


@pytest.mark.parametrize(
    ("outcome", "expected_state"),
    [
        (DispatchOutcome.SENT, "sent"),
        (DispatchOutcome.RETRY, "retry"),
        (DispatchOutcome.DEAD_LETTER, "dead_letter"),
    ],
)
async def test_finish_records_each_outcome_and_redacts_error(
    outcome: DispatchOutcome,
    expected_state: str,
) -> None:
    session = FakeSession()
    item = OutboxItem(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "oa.complete_pending",
        "dedup",
        {},
        1,
        NOW,
    )

    await repository(session).finish(
        item,
        DispatchResult(
            success=outcome is DispatchOutcome.SENT,
            status_code=503,
            error_code="OA_UNAVAILABLE",
            redacted_error="x" * 600,
        ),
        outcome=outcome,
        available_at=NOW + timedelta(seconds=30),
        now=NOW,
    )

    attempt = next(value for value in session.added if isinstance(value, OutboxAttempt))
    assert attempt.result == expected_state
    assert attempt.status_code == 503
    assert attempt.redacted_error == "x" * 512


async def test_finish_uses_none_for_empty_error() -> None:
    session = FakeSession()
    item = OutboxItem(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "oa.complete_pending",
        "dedup",
        {},
        1,
        NOW,
    )
    await repository(session).finish(
        item,
        DispatchResult(success=True),
        outcome=DispatchOutcome.SENT,
        available_at=NOW,
        now=NOW,
    )
    attempt = next(value for value in session.added if isinstance(value, OutboxAttempt))
    assert attempt.redacted_error is None


async def test_finish_fails_when_lease_was_lost() -> None:
    session = FakeSession(updated_id=None)
    item = OutboxItem(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "oa.complete_pending",
        "dedup",
        {},
        1,
        NOW,
    )
    with pytest.raises(RuntimeError, match="lease was lost"):
        await repository(session).finish(
            item,
            DispatchResult(success=True),
            outcome=DispatchOutcome.SENT,
            available_at=NOW,
            now=NOW,
        )
