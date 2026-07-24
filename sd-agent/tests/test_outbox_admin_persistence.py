from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

import pytest

from sd_agent.outbox.admin import AdminActor, OutboxAdminError
from sd_agent.persistence import outbox_admin as outbox_admin_module
from sd_agent.persistence.models import AuditEvent, OutboxMessage, OutboxReplayApproval
from sd_agent.persistence.outbox_admin import SqlOutboxAdminRepository

NOW = datetime(2026, 7, 24, 2, 0, tzinfo=UTC)
OUTBOX_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
APPROVAL_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
APPROVAL_KEY = UUID("11111111-1111-4111-8111-111111111111")
EXECUTION_KEY = UUID("22222222-2222-4222-8222-222222222222")
REASON = "OA 服务恢复后经人工核对允许补发"


def actor(person_id: int, role: str) -> AdminActor:
    return AdminActor(person_id, frozenset({role}), f"req_{person_id}", "192.0.2.1", "ua")


def message(*, state: str = "dead_letter", outbox_id: str = OUTBOX_ID) -> OutboxMessage:
    return OutboxMessage(
        outbox_id=outbox_id,
        kind="oa.complete_pending",
        dedup_key=f"submission:{outbox_id}",
        payload={"task_id": 101},
        state=state,
        available_at=NOW,
        lease_until=None,
        attempt_count=6,
        last_error_code="OA_UNAVAILABLE",
        created_at=NOW,
        updated_at=NOW,
    )


def approval(
    *,
    outbox_id: str = OUTBOX_ID,
    approval_id: str = APPROVAL_ID,
    approved_by: int = 7,
    consumed: bool = False,
    approval_key: str | None = None,
    execution_key: str | None = None,
    reason: str = REASON,
) -> OutboxReplayApproval:
    return OutboxReplayApproval(
        approval_id=approval_id,
        outbox_id=outbox_id,
        approval_idempotency_key=approval_key or str(APPROVAL_KEY),
        reason=reason,
        reason_hash=sha256(reason.encode()).hexdigest(),
        approved_by=approved_by,
        approved_at=NOW,
        consumed_by=8 if consumed else None,
        consumed_at=NOW if consumed else None,
        execution_idempotency_key=execution_key,
    )


class FakeSession:
    def __init__(
        self,
        *,
        scalar_values: list[object | None] | None = None,
        scalar_rows: list[OutboxMessage] | None = None,
    ) -> None:
        self.scalar_values = list(scalar_values or [])
        self.scalar_rows = list(scalar_rows or [])
        self.added: list[object] = []

    async def scalar(self, _statement: object) -> object | None:
        return self.scalar_values.pop(0)

    async def scalars(self, _statement: object) -> list[OutboxMessage]:
        return self.scalar_rows

    def add(self, value: object) -> None:
        self.added.append(value)


class FakeSessions:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[FakeSession]:
        yield self.session

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeSession]:
        yield self.session


def repository(session: FakeSession) -> SqlOutboxAdminRepository:
    instance = object.__new__(SqlOutboxAdminRepository)
    instance._sessions = FakeSessions(session)  # type: ignore[assignment]
    return instance


def test_repository_builds_non_expiring_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[object, bool]] = []

    def factory(engine: object, *, expire_on_commit: bool) -> object:
        seen.append((engine, expire_on_commit))
        return object()

    monkeypatch.setattr(
        outbox_admin_module,
        "async_sessionmaker",
        factory,
    )
    engine = object()
    SqlOutboxAdminRepository(engine)  # type: ignore[arg-type]
    assert seen == [(engine, False)]


async def test_list_dead_letters_uses_cursor_page_without_exposing_payload() -> None:
    rows = [
        message(outbox_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"),
        message(outbox_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2"),
    ]

    items, cursor = await repository(FakeSession(scalar_rows=rows)).list_dead_letters(
        cursor="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa0",
        limit=1,
    )

    assert len(items) == 1
    assert items[0].outbox_id == rows[0].outbox_id
    assert cursor == rows[0].outbox_id
    assert not hasattr(items[0], "payload")


async def test_list_dead_letters_empty_page_has_no_cursor() -> None:
    items, cursor = await repository(FakeSession(scalar_rows=[])).list_dead_letters(
        cursor=None,
        limit=25,
    )
    assert items == ()
    assert cursor is None


async def test_approve_replay_is_transactional_and_audited() -> None:
    session = FakeSession(scalar_values=[None, message(), None])

    result = await repository(session).approve_replay(
        outbox_id=OUTBOX_ID,
        reason=REASON,
        idempotency_key=APPROVAL_KEY,
        actor=actor(7, "supervision_admin"),
        now=NOW,
    )

    assert result.outbox_id == OUTBOX_ID
    stored = next(value for value in session.added if isinstance(value, OutboxReplayApproval))
    audit = next(value for value in session.added if isinstance(value, AuditEvent))
    assert stored.reason_hash == sha256(REASON.encode()).hexdigest()
    assert audit.what == "outbox.replay.approve"
    assert REASON not in str(audit.details)


async def test_approve_replay_reuses_matching_idempotency_key() -> None:
    existing = approval()
    session = FakeSession(scalar_values=[existing])

    result = await repository(session).approve_replay(
        outbox_id=OUTBOX_ID,
        reason=REASON,
        idempotency_key=APPROVAL_KEY,
        actor=actor(7, "supervision_admin"),
        now=NOW,
    )

    assert result.approval_id == APPROVAL_ID
    assert session.added == []


@pytest.mark.parametrize(
    ("scalars", "code"),
    [
        ([approval(outbox_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc")], "IDEMPOTENCY_CONFLICT"),
        ([None, None], "OUTBOX_NOT_FOUND"),
        ([None, message(state="sent")], "OUTBOX_NOT_DEAD"),
        ([None, message(), approval()], "OUTBOX_ALREADY_APPROVED"),
    ],
)
async def test_approve_replay_rejects_invalid_state(
    scalars: list[object | None],
    code: str,
) -> None:
    with pytest.raises(OutboxAdminError) as caught:
        await repository(FakeSession(scalar_values=scalars)).approve_replay(
            outbox_id=OUTBOX_ID,
            reason=REASON,
            idempotency_key=APPROVAL_KEY,
            actor=actor(7, "supervision_admin"),
            now=NOW,
        )
    assert caught.value.code == code


async def test_replay_consumes_approval_resets_retry_budget_and_audits() -> None:
    row = approval()
    outbox = message()
    session = FakeSession(scalar_values=[None, row, outbox])

    result = await repository(session).replay(
        outbox_id=OUTBOX_ID,
        approval_id=APPROVAL_ID,
        idempotency_key=EXECUTION_KEY,
        actor=actor(8, "ops_admin"),
        now=NOW,
    )

    assert result.state == "retry"
    assert result.idempotent is False
    assert row.consumed_by == 8
    assert row.execution_idempotency_key == str(EXECUTION_KEY)
    assert outbox.state == "retry"
    assert outbox.attempt_count == 0
    assert outbox.last_error_code is None
    audit = next(value for value in session.added if isinstance(value, AuditEvent))
    assert audit.what == "outbox.replay.execute"


async def test_replay_reuses_matching_execution_idempotency_key() -> None:
    previous = approval(execution_key=str(EXECUTION_KEY), consumed=True)

    result = await repository(FakeSession(scalar_values=[previous])).replay(
        outbox_id=OUTBOX_ID,
        approval_id=APPROVAL_ID,
        idempotency_key=EXECUTION_KEY,
        actor=actor(8, "ops_admin"),
        now=NOW,
    )

    assert result.idempotent is True


@pytest.mark.parametrize(
    ("scalars", "person_id", "code"),
    [
        (
            [approval(outbox_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc", consumed=True)],
            8,
            "IDEMPOTENCY_CONFLICT",
        ),
        ([None, None], 8, "OUTBOX_APPROVAL_NOT_FOUND"),
        ([None, approval(consumed=True)], 8, "OUTBOX_APPROVAL_CONSUMED"),
        ([None, approval()], 7, "OUTBOX_TWO_PERSON_REQUIRED"),
        ([None, approval(), None], 8, "OUTBOX_NOT_FOUND"),
        ([None, approval(), message(state="sent")], 8, "OUTBOX_NOT_DEAD"),
    ],
)
async def test_replay_rejects_invalid_approval_or_message_state(
    scalars: list[object | None],
    person_id: int,
    code: str,
) -> None:
    with pytest.raises(OutboxAdminError) as caught:
        await repository(FakeSession(scalar_values=scalars)).replay(
            outbox_id=OUTBOX_ID,
            approval_id=APPROVAL_ID,
            idempotency_key=EXECUTION_KEY,
            actor=actor(person_id, "ops_admin"),
            now=NOW,
        )
    assert caught.value.code == code
