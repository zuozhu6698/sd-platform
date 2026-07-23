from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from sd_agent.auth.service import SessionState
from sd_agent.persistence.models import AuthSession


class SqlSessionStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def get(self, sid: str) -> SessionState | None:
        async with self._sessions() as session:
            row = await session.scalar(select(AuthSession).where(AuthSession.sid == sid))
        if row is None:
            return None
        return SessionState(
            sid=row.sid,
            person_id=row.person_id,
            kid=row.kid,
            csrf_hash=row.csrf_hash,
            expires_at=row.expires_at,
            revoked_at=row.revoked_at,
        )

    async def update_csrf(self, sid: str, csrf_hash: str, *, now: datetime) -> bool:
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(AuthSession)
                .where(AuthSession.sid == sid, AuthSession.revoked_at.is_(None))
                .values(csrf_hash=csrf_hash, last_seen_at=now)
                .returning(AuthSession.sid)
            )
        return result.scalar_one_or_none() is not None

    async def revoke(self, sid: str, *, now: datetime) -> bool:
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(AuthSession)
                .where(AuthSession.sid == sid, AuthSession.revoked_at.is_(None))
                .values(revoked_at=now, last_seen_at=now)
                .returning(AuthSession.sid)
            )
        return result.scalar_one_or_none() is not None
