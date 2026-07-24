from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sd_agent.auth.security import CsrfProtector, Principal, TokenService


@dataclass(frozen=True, slots=True)
class SessionState:
    sid: str
    person_id: int
    kid: str
    csrf_hash: str
    expires_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class PersonIdentity:
    person_id: int
    name: str
    unit_id: int
    active: bool
    authz_version: int


@dataclass(frozen=True, slots=True)
class RoleScope:
    role: str
    scope_unit_id: int | None


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    principal: Principal
    session: SessionState
    person: PersonIdentity
    roles: tuple[RoleScope, ...]


class SessionStore(Protocol):
    async def get(self, sid: str) -> SessionState | None: ...

    async def update_csrf(self, sid: str, csrf_hash: str, *, now: datetime) -> bool: ...

    async def revoke(self, sid: str, *, now: datetime) -> bool: ...


class IdentityStore(Protocol):
    async def get_person(self, person_id: int) -> PersonIdentity | None: ...

    async def get_active_roles(self, person_id: int, *, now: datetime) -> tuple[RoleScope, ...]: ...


class AuthenticationError(ValueError):
    pass


class AuthService:
    def __init__(
        self,
        *,
        tokens: TokenService,
        csrf: CsrfProtector,
        sessions: SessionStore,
        identities: IdentityStore,
    ) -> None:
        self._tokens = tokens
        self._csrf = csrf
        self._sessions = sessions
        self._identities = identities

    async def authenticate(self, token: str, *, now: datetime) -> AuthenticatedUser:
        current_time = _require_utc(now)
        try:
            principal = self._tokens.verify(token, now=current_time)
        except ValueError as exc:
            raise AuthenticationError("session is invalid") from exc
        session = await self._sessions.get(principal.sid)
        if session is None:
            raise AuthenticationError("session is invalid")
        if (
            session.person_id != principal.person_id
            or session.kid != principal.kid
            or session.revoked_at is not None
            or session.expires_at <= current_time
            or principal.expires_at > session.expires_at
        ):
            raise AuthenticationError("session is invalid")
        person = await self._identities.get_person(principal.person_id)
        if person is None or not person.active:
            raise AuthenticationError("session is invalid")
        roles = await self._identities.get_active_roles(person.person_id, now=current_time)
        return AuthenticatedUser(principal, session, person, roles)

    async def issue_csrf(self, user: AuthenticatedUser, *, now: datetime) -> str:
        current_time = _require_utc(now)
        token, digest = self._csrf.issue(user.session.sid)
        updated = await self._sessions.update_csrf(user.session.sid, digest, now=current_time)
        if not updated:
            raise AuthenticationError("session is invalid")
        return token

    def verify_csrf(self, user: AuthenticatedUser, token: str | None) -> None:
        if not token or not self._csrf.verify(
            sid=user.session.sid,
            token=token,
            expected_digest=user.session.csrf_hash,
        ):
            raise AuthenticationError("csrf validation failed")

    async def logout(self, user: AuthenticatedUser, *, now: datetime) -> None:
        current_time = _require_utc(now)
        await self._sessions.revoke(user.session.sid, now=current_time)


def capabilities(roles: tuple[RoleScope, ...]) -> dict[str, bool]:
    names = {role.role for role in roles}
    return {
        "report": bool(names & {"domain_owner", "unit_coordinator", "supervision_admin"}),
        "review": "supervision_admin" in names,
        "issue_report": bool(names & {"leader", "supervision_admin"}),
        "outbox_view": bool(names & {"supervision_admin", "ops_admin"}),
        "outbox_replay_approve": "supervision_admin" in names,
        "outbox_replay_execute": "ops_admin" in names,
    }


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("datetime must be timezone-aware UTC")
    return value.astimezone(UTC)
