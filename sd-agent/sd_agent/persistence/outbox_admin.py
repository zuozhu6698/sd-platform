from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from sd_agent.outbox.admin import (
    AdminActor,
    DeadLetterSummary,
    OutboxAdminError,
    ReplayApproval,
    ReplayResult,
)
from sd_agent.persistence.models import AuditEvent, OutboxMessage, OutboxReplayApproval


class SqlOutboxAdminRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def list_dead_letters(
        self,
        *,
        cursor: str | None,
        limit: int,
    ) -> tuple[tuple[DeadLetterSummary, ...], str | None]:
        statement = (
            select(OutboxMessage)
            .where(OutboxMessage.state == "dead_letter")
            .order_by(OutboxMessage.outbox_id)
            .limit(limit + 1)
        )
        if cursor is not None:
            statement = statement.where(OutboxMessage.outbox_id > cursor)
        async with self._sessions() as session:
            rows = list(await session.scalars(statement))
        has_next = len(rows) > limit
        visible = rows[:limit]
        items = tuple(
            DeadLetterSummary(
                outbox_id=row.outbox_id,
                kind=row.kind,
                state=row.state,
                attempt_count=row.attempt_count,
                last_error_code=row.last_error_code,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in visible
        )
        next_cursor = visible[-1].outbox_id if has_next and visible else None
        return items, next_cursor

    async def approve_replay(
        self,
        *,
        outbox_id: str,
        reason: str,
        idempotency_key: UUID,
        actor: AdminActor,
        now: datetime,
    ) -> ReplayApproval:
        current_time = _as_datetime(now)
        reason_hash = sha256(reason.encode("utf-8")).hexdigest()
        key = str(idempotency_key)
        async with self._sessions.begin() as session:
            existing = await session.scalar(
                select(OutboxReplayApproval).where(
                    OutboxReplayApproval.approval_idempotency_key == key
                )
            )
            if existing is not None:
                if existing.outbox_id != outbox_id or existing.reason_hash != reason_hash:
                    raise OutboxAdminError(
                        "IDEMPOTENCY_CONFLICT",
                        "幂等键已用于其他审批请求",
                        409,
                    )
                return _approval(existing)

            message = await session.scalar(
                select(OutboxMessage).where(OutboxMessage.outbox_id == outbox_id).with_for_update()
            )
            if message is None:
                raise OutboxAdminError("OUTBOX_NOT_FOUND", "消息不存在", 404)
            if message.state != "dead_letter":
                raise OutboxAdminError("OUTBOX_NOT_DEAD", "消息不处于死信状态", 409)
            active = await session.scalar(
                select(OutboxReplayApproval).where(
                    OutboxReplayApproval.outbox_id == outbox_id,
                    OutboxReplayApproval.consumed_at.is_(None),
                )
            )
            if active is not None:
                raise OutboxAdminError(
                    "OUTBOX_ALREADY_APPROVED",
                    "该消息已有待执行审批",
                    409,
                )

            row = OutboxReplayApproval(
                approval_id=str(uuid4()),
                outbox_id=outbox_id,
                approval_idempotency_key=key,
                reason=reason,
                reason_hash=reason_hash,
                approved_by=actor.person_id,
                approved_at=current_time,
                consumed_by=None,
                consumed_at=None,
                execution_idempotency_key=None,
            )
            session.add(row)
            session.add(
                _audit(
                    actor,
                    what="outbox.replay.approve",
                    target_id=outbox_id,
                    result="approved",
                    details={"approval_id": row.approval_id, "reason_hash": reason_hash},
                    now=current_time,
                )
            )
            return _approval(row)

    async def replay(
        self,
        *,
        outbox_id: str,
        approval_id: str,
        idempotency_key: UUID,
        actor: AdminActor,
        now: datetime,
    ) -> ReplayResult:
        current_time = _as_datetime(now)
        key = str(idempotency_key)
        async with self._sessions.begin() as session:
            previous = await session.scalar(
                select(OutboxReplayApproval).where(
                    OutboxReplayApproval.execution_idempotency_key == key
                )
            )
            if previous is not None:
                if previous.outbox_id != outbox_id or previous.approval_id != approval_id:
                    raise OutboxAdminError(
                        "IDEMPOTENCY_CONFLICT",
                        "幂等键已用于其他补发请求",
                        409,
                    )
                return ReplayResult(outbox_id, approval_id, "retry", True)

            approval = await session.scalar(
                select(OutboxReplayApproval)
                .where(OutboxReplayApproval.approval_id == approval_id)
                .with_for_update()
            )
            if approval is None or approval.outbox_id != outbox_id:
                raise OutboxAdminError("OUTBOX_APPROVAL_NOT_FOUND", "补发审批不存在", 404)
            if approval.consumed_at is not None:
                raise OutboxAdminError("OUTBOX_APPROVAL_CONSUMED", "补发审批已使用", 409)
            if approval.approved_by == actor.person_id:
                raise OutboxAdminError(
                    "OUTBOX_TWO_PERSON_REQUIRED",
                    "审批人与执行人必须为不同人员",
                    403,
                )
            message = await session.scalar(
                select(OutboxMessage).where(OutboxMessage.outbox_id == outbox_id).with_for_update()
            )
            if message is None:
                raise OutboxAdminError("OUTBOX_NOT_FOUND", "消息不存在", 404)
            if message.state != "dead_letter":
                raise OutboxAdminError("OUTBOX_NOT_DEAD", "消息不处于死信状态", 409)

            approval.consumed_by = actor.person_id
            approval.consumed_at = current_time
            approval.execution_idempotency_key = key
            message.state = "retry"
            message.available_at = current_time
            message.lease_until = None
            message.attempt_count = 0
            message.last_error_code = None
            message.updated_at = current_time
            session.add(
                _audit(
                    actor,
                    what="outbox.replay.execute",
                    target_id=outbox_id,
                    result="queued",
                    details={"approval_id": approval_id},
                    now=current_time,
                )
            )
            return ReplayResult(outbox_id, approval_id, "retry", False)


def _approval(row: OutboxReplayApproval) -> ReplayApproval:
    return ReplayApproval(
        approval_id=row.approval_id,
        outbox_id=row.outbox_id,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        consumed=row.consumed_at is not None,
    )


def _audit(
    actor: AdminActor,
    *,
    what: str,
    target_id: str,
    result: str,
    details: dict[str, str],
    now: datetime,
) -> AuditEvent:
    current_time = _as_datetime(now)
    return AuditEvent(
        event_id=str(uuid4()),
        request_id=actor.request_id,
        who=str(actor.person_id),
        role=",".join(sorted(actor.roles))[:64] or None,
        scope={},
        what=what,
        target_type="outbox_message",
        target_id=target_id,
        result=result,
        ip=actor.ip,
        user_agent=actor.user_agent,
        details=details,
        created_at=current_time,
    )


def _as_datetime(value: datetime) -> datetime:
    return value
