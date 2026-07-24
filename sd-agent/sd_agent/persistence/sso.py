from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from sd_agent.persistence.models import AuditEvent, AuthSession, SsoLoginAttempt
from sd_agent.sso.service import PendingSsoAttempt


class SqlSsoPersistence:
    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def create_attempt(self, attempt: PendingSsoAttempt) -> None:
        async with self._sessions.begin() as session:
            session.add(
                SsoLoginAttempt(
                    attempt_id=str(uuid4()),
                    state_hash=attempt.state_hash,
                    nonce_hash=attempt.nonce_hash,
                    redirect_path=attempt.redirect_path,
                    ticket_hash=None,
                    created_at=attempt.created_at,
                    expires_at=attempt.expires_at,
                    consumed_at=None,
                )
            )

    async def get_pending_attempt(
        self,
        state_hash: str,
        *,
        now: datetime,
    ) -> PendingSsoAttempt | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(SsoLoginAttempt).where(
                    SsoLoginAttempt.state_hash == state_hash,
                    SsoLoginAttempt.consumed_at.is_(None),
                    SsoLoginAttempt.expires_at > now,
                )
            )
        return _attempt(row) if row is not None else None

    async def consume_and_create_session(
        self,
        *,
        state_hash: str,
        ticket_hash: str,
        person_id: int,
        sid: str,
        kid: str,
        expires_at: datetime,
        request_id: str,
        now: datetime,
    ) -> str | None:
        async with self._sessions.begin() as session:
            await session.scalar(
                select(func.pg_advisory_xact_lock(func.hashtextextended(ticket_hash, 0)))
            )
            duplicate_ticket = await session.scalar(
                select(SsoLoginAttempt.attempt_id).where(SsoLoginAttempt.ticket_hash == ticket_hash)
            )
            if duplicate_ticket is not None:
                return None
            row = await session.scalar(
                select(SsoLoginAttempt)
                .where(SsoLoginAttempt.state_hash == state_hash)
                .with_for_update()
            )
            if row is None or row.consumed_at is not None or row.expires_at <= now:
                return None
            row.ticket_hash = ticket_hash
            row.consumed_at = now
            session.add(
                AuthSession(
                    sid=sid,
                    person_id=person_id,
                    kid=kid,
                    csrf_hash="0" * 64,
                    expires_at=expires_at,
                    revoked_at=None,
                    last_seen_at=now,
                    created_at=now,
                )
            )
            session.add(
                AuditEvent(
                    event_id=str(uuid4()),
                    request_id=request_id,
                    who=str(person_id),
                    role=None,
                    scope={},
                    what="auth.sso.login",
                    target_type="auth_session",
                    target_id=sid,
                    result="success",
                    ip=None,
                    user_agent=None,
                    details={"provider": "oa"},
                    created_at=now,
                )
            )
            return row.redirect_path

    async def record_failure(
        self,
        *,
        state_hash: str,
        request_id: str,
        error_code: str,
        now: datetime,
    ) -> None:
        async with self._sessions.begin() as session:
            attempt = await session.scalar(
                select(SsoLoginAttempt)
                .where(SsoLoginAttempt.state_hash == state_hash)
                .with_for_update()
            )
            if attempt is not None and attempt.consumed_at is None and attempt.expires_at > now:
                attempt.consumed_at = now
            session.add(
                AuditEvent(
                    event_id=str(uuid4()),
                    request_id=request_id,
                    who="anonymous",
                    role=None,
                    scope={},
                    what="auth.sso.login",
                    target_type="sso_state",
                    target_id=state_hash[:16],
                    result="failed",
                    ip=None,
                    user_agent=None,
                    details={"error_code": error_code, "provider": "oa"},
                    created_at=now,
                )
            )


def _attempt(row: SsoLoginAttempt) -> PendingSsoAttempt:
    return PendingSsoAttempt(
        state_hash=row.state_hash,
        nonce_hash=row.nonce_hash,
        redirect_path=row.redirect_path,
        created_at=row.created_at,
        expires_at=row.expires_at,
        consumed_at=row.consumed_at,
    )
