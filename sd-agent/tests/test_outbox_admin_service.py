from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from sd_agent.outbox.admin import (
    AdminActor,
    DeadLetterSummary,
    OutboxAdminError,
    OutboxAdminService,
    ReplayApproval,
    ReplayResult,
)

NOW = datetime(2026, 7, 24, 2, 0, tzinfo=UTC)
APPROVAL_KEY = UUID("11111111-1111-4111-8111-111111111111")
EXECUTION_KEY = UUID("22222222-2222-4222-8222-222222222222")


def actor(person_id: int, *roles: str) -> AdminActor:
    return AdminActor(
        person_id=person_id,
        roles=frozenset(roles),
        request_id=f"req_{person_id}",
        ip="192.0.2.1",
        user_agent="test-agent",
    )


class FakeRepository:
    def __init__(self) -> None:
        self.dead_letters = (
            DeadLetterSummary(
                outbox_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                kind="oa.complete_pending",
                state="dead_letter",
                attempt_count=6,
                last_error_code="OA_UNAVAILABLE",
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        self.approval = ReplayApproval(
            approval_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            outbox_id=self.dead_letters[0].outbox_id,
            approved_by=7,
            approved_at=NOW,
            consumed=False,
        )
        self.approve_args: tuple[object, ...] | None = None
        self.replay_args: tuple[object, ...] | None = None

    async def list_dead_letters(
        self,
        *,
        cursor: str | None,
        limit: int,
    ) -> tuple[tuple[DeadLetterSummary, ...], str | None]:
        assert cursor is None
        assert limit == 25
        return self.dead_letters, "next-cursor"

    async def approve_replay(
        self,
        *,
        outbox_id: str,
        reason: str,
        idempotency_key: UUID,
        actor: AdminActor,
        now: datetime,
    ) -> ReplayApproval:
        self.approve_args = (outbox_id, reason, idempotency_key, actor, now)
        return self.approval

    async def replay(
        self,
        *,
        outbox_id: str,
        approval_id: str,
        idempotency_key: UUID,
        actor: AdminActor,
        now: datetime,
    ) -> ReplayResult:
        self.replay_args = (outbox_id, approval_id, idempotency_key, actor, now)
        return ReplayResult(outbox_id, approval_id, "retry", True)


async def test_authorized_roles_list_safe_dead_letter_metadata() -> None:
    repository = FakeRepository()
    service = OutboxAdminService(repository)

    page = await service.list_dead_letters(
        actor=actor(7, "supervision_admin"),
        cursor=None,
        limit=25,
    )

    assert page.items == repository.dead_letters
    assert page.next_cursor == "next-cursor"
    assert not hasattr(page.items[0], "payload")


async def test_list_rejects_invalid_limit() -> None:
    service = OutboxAdminService(FakeRepository())
    with pytest.raises(OutboxAdminError, match="OUTBOX_LIMIT_INVALID"):
        await service.list_dead_letters(
            actor=actor(7, "ops_admin"),
            cursor=None,
            limit=101,
        )


@pytest.mark.parametrize("role", ["domain_owner", "leader", "unit_coordinator"])
async def test_non_admin_roles_cannot_list_dead_letters(role: str) -> None:
    service = OutboxAdminService(FakeRepository())

    with pytest.raises(OutboxAdminError, match="OUTBOX_FORBIDDEN"):
        await service.list_dead_letters(actor=actor(9, role), cursor=None, limit=25)


async def test_supervision_admin_can_approve_replay() -> None:
    repository = FakeRepository()
    service = OutboxAdminService(repository)
    approver = actor(7, "supervision_admin")

    result = await service.approve_replay(
        outbox_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        reason="OA 服务恢复后经人工核对允许补发",
        idempotency_key=APPROVAL_KEY,
        actor=approver,
        now=NOW,
    )

    assert result == repository.approval
    assert repository.approve_args == (
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "OA 服务恢复后经人工核对允许补发",
        APPROVAL_KEY,
        approver,
        NOW,
    )


async def test_only_ops_admin_can_execute_replay() -> None:
    repository = FakeRepository()
    service = OutboxAdminService(repository)
    executor = actor(8, "ops_admin")

    result = await service.replay(
        outbox_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        approval_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        idempotency_key=EXECUTION_KEY,
        actor=executor,
        now=NOW,
    )

    assert result.state == "retry"
    assert result.idempotent is True
    assert repository.replay_args == (
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        EXECUTION_KEY,
        executor,
        NOW,
    )


@pytest.mark.parametrize(
    ("operation", "roles"),
    [("approve", ("ops_admin",)), ("replay", ("supervision_admin",))],
)
async def test_approval_and_execution_roles_are_separated(
    operation: str,
    roles: tuple[str, ...],
) -> None:
    service = OutboxAdminService(FakeRepository())
    current_actor = actor(7, *roles)

    with pytest.raises(OutboxAdminError, match="OUTBOX_FORBIDDEN"):
        if operation == "approve":
            await service.approve_replay(
                outbox_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                reason="人工核对完成并允许补发此消息",
                idempotency_key=APPROVAL_KEY,
                actor=current_actor,
                now=NOW,
            )
        else:
            await service.replay(
                outbox_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                approval_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                idempotency_key=EXECUTION_KEY,
                actor=current_actor,
                now=NOW,
            )


@pytest.mark.parametrize("reason", ["", "太短", "x" * 501])
async def test_approval_reason_is_bounded(reason: str) -> None:
    service = OutboxAdminService(FakeRepository())

    with pytest.raises(OutboxAdminError, match="OUTBOX_REASON_INVALID"):
        await service.approve_replay(
            outbox_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            reason=reason,
            idempotency_key=APPROVAL_KEY,
            actor=actor(7, "supervision_admin"),
            now=NOW,
        )


@pytest.mark.parametrize(
    "operation",
    ["approve", "replay"],
)
async def test_write_operations_require_utc(operation: str) -> None:
    service = OutboxAdminService(FakeRepository())
    local_time = NOW.astimezone(UTC).replace(tzinfo=None)
    with pytest.raises(ValueError, match="UTC"):
        if operation == "approve":
            await service.approve_replay(
                outbox_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                reason="人工核对完成并允许补发此消息",
                idempotency_key=APPROVAL_KEY,
                actor=actor(7, "supervision_admin"),
                now=local_time,
            )
        else:
            await service.replay(
                outbox_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                approval_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                idempotency_key=EXECUTION_KEY,
                actor=actor(8, "ops_admin"),
                now=NOW.astimezone(UTC).astimezone(timezone(timedelta(hours=8))),
            )
