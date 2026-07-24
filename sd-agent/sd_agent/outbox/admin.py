from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AdminActor:
    person_id: int
    roles: frozenset[str]
    request_id: str
    ip: str | None
    user_agent: str | None


@dataclass(frozen=True, slots=True)
class DeadLetterSummary:
    outbox_id: str
    kind: str
    state: str
    attempt_count: int
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DeadLetterPage:
    items: tuple[DeadLetterSummary, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ReplayApproval:
    approval_id: str
    outbox_id: str
    approved_by: int
    approved_at: datetime
    consumed: bool


@dataclass(frozen=True, slots=True)
class ReplayResult:
    outbox_id: str
    approval_id: str
    state: str
    idempotent: bool


class OutboxAdminError(ValueError):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.status_code = status_code


class OutboxAdminRepository(Protocol):
    async def list_dead_letters(
        self,
        *,
        cursor: str | None,
        limit: int,
    ) -> tuple[tuple[DeadLetterSummary, ...], str | None]: ...

    async def approve_replay(
        self,
        *,
        outbox_id: str,
        reason: str,
        idempotency_key: UUID,
        actor: AdminActor,
        now: datetime,
    ) -> ReplayApproval: ...

    async def replay(
        self,
        *,
        outbox_id: str,
        approval_id: str,
        idempotency_key: UUID,
        actor: AdminActor,
        now: datetime,
    ) -> ReplayResult: ...


class OutboxAdminService:
    def __init__(self, repository: OutboxAdminRepository) -> None:
        self._repository = repository

    async def list_dead_letters(
        self,
        *,
        actor: AdminActor,
        cursor: str | None,
        limit: int,
    ) -> DeadLetterPage:
        _require_role(actor, {"supervision_admin", "ops_admin"})
        if not 1 <= limit <= 100:
            raise OutboxAdminError("OUTBOX_LIMIT_INVALID", "分页数量无效", 422)
        items, next_cursor = await self._repository.list_dead_letters(
            cursor=cursor,
            limit=limit,
        )
        return DeadLetterPage(items, next_cursor)

    async def approve_replay(
        self,
        *,
        outbox_id: str,
        reason: str,
        idempotency_key: UUID,
        actor: AdminActor,
        now: datetime,
    ) -> ReplayApproval:
        _require_role(actor, {"supervision_admin"})
        normalized_reason = reason.strip()
        if not 10 <= len(normalized_reason) <= 500:
            raise OutboxAdminError(
                "OUTBOX_REASON_INVALID",
                "补发审批原因需为 10–500 个字符",
                422,
            )
        return await self._repository.approve_replay(
            outbox_id=outbox_id,
            reason=normalized_reason,
            idempotency_key=idempotency_key,
            actor=actor,
            now=_require_utc(now),
        )

    async def replay(
        self,
        *,
        outbox_id: str,
        approval_id: str,
        idempotency_key: UUID,
        actor: AdminActor,
        now: datetime,
    ) -> ReplayResult:
        _require_role(actor, {"ops_admin"})
        return await self._repository.replay(
            outbox_id=outbox_id,
            approval_id=approval_id,
            idempotency_key=idempotency_key,
            actor=actor,
            now=_require_utc(now),
        )


def _require_role(actor: AdminActor, allowed: set[str]) -> None:
    if actor.roles.isdisjoint(allowed):
        raise OutboxAdminError("OUTBOX_FORBIDDEN", "无权执行该管理操作", 403)


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("datetime must be timezone-aware UTC")
    return value.astimezone(UTC)
