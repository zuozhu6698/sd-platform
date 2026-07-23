from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from sd_agent.auth import CsrfProtector, TokenService
from sd_agent.auth.service import (
    AuthenticationError,
    AuthService,
    PersonIdentity,
    RoleScope,
    SessionState,
    capabilities,
)

NOW = datetime(2026, 7, 23, 4, 0, tzinfo=UTC)
JWT_KEY = "j" * 32
CSRF_KEY = "c" * 32


class FakeSessions:
    def __init__(self, state: SessionState | None) -> None:
        self.state = state
        self.allow_update = True

    async def get(self, sid: str) -> SessionState | None:
        if self.state is None or self.state.sid != sid:
            return None
        return self.state

    async def update_csrf(self, sid: str, csrf_hash: str, *, now: datetime) -> bool:
        if not self.allow_update or self.state is None or self.state.sid != sid:
            return False
        self.state = replace(self.state, csrf_hash=csrf_hash)
        return True

    async def revoke(self, sid: str, *, now: datetime) -> bool:
        if self.state is None or self.state.sid != sid or self.state.revoked_at is not None:
            return False
        self.state = replace(self.state, revoked_at=now)
        return True


class FakeIdentities:
    def __init__(self) -> None:
        self.person: PersonIdentity | None = PersonIdentity(7, "张三", 10, True, 2)
        self.roles = (RoleScope("domain_owner", 10),)

    async def get_person(self, person_id: int) -> PersonIdentity | None:
        return self.person if self.person and self.person.person_id == person_id else None

    async def get_active_roles(self, person_id: int, *, now: datetime) -> tuple[RoleScope, ...]:
        return self.roles


def build_service(
    *,
    session: SessionState | None = None,
) -> tuple[AuthService, FakeSessions, FakeIdentities, str]:
    tokens = TokenService(active_kid="v1", keys={"v1": JWT_KEY}, expire_minutes=60)
    token = tokens.issue(person_id=7, sid="sid_7", now=NOW)
    sessions = FakeSessions(
        session
        or SessionState(
            "sid_7",
            7,
            "v1",
            "initial-csrf-hash",
            NOW + timedelta(minutes=60),
            None,
        )
    )
    identities = FakeIdentities()
    service = AuthService(
        tokens=tokens,
        csrf=CsrfProtector(CSRF_KEY),
        sessions=sessions,
        identities=identities,
    )
    return service, sessions, identities, token


async def test_authenticate_loads_current_person_and_roles() -> None:
    service, _, _, token = build_service()
    user = await service.authenticate(token, now=NOW + timedelta(minutes=1))
    assert user.person.name == "张三"
    assert user.roles == (RoleScope("domain_owner", 10),)


@pytest.mark.parametrize(
    "session",
    [
        None,
        SessionState("sid_7", 8, "v1", "h", NOW + timedelta(hours=1), None),
        SessionState("sid_7", 7, "v2", "h", NOW + timedelta(hours=1), None),
        SessionState("sid_7", 7, "v1", "h", NOW + timedelta(hours=1), NOW),
        SessionState("sid_7", 7, "v1", "h", NOW, None),
        SessionState("sid_7", 7, "v1", "h", NOW + timedelta(minutes=59), None),
    ],
)
async def test_authenticate_rejects_invalid_session_contract(
    session: SessionState | None,
) -> None:
    service, sessions, _, token = build_service()
    sessions.state = session
    with pytest.raises(AuthenticationError):
        await service.authenticate(token, now=NOW)


async def test_authenticate_rejects_missing_or_inactive_identity() -> None:
    service, _, identities, token = build_service()
    identities.person = None
    with pytest.raises(AuthenticationError):
        await service.authenticate(token, now=NOW)
    identities.person = PersonIdentity(7, "张三", 10, False, 2)
    with pytest.raises(AuthenticationError):
        await service.authenticate(token, now=NOW)


async def test_authenticate_normalizes_token_failure() -> None:
    service, _, _, _ = build_service()
    with pytest.raises(AuthenticationError, match="session"):
        await service.authenticate("bad-token", now=NOW)


async def test_csrf_rotation_verification_and_logout() -> None:
    service, sessions, _, token = build_service()
    user = await service.authenticate(token, now=NOW)
    csrf_token = await service.issue_csrf(user, now=NOW)
    assert sessions.state is not None
    refreshed = await service.authenticate(token, now=NOW)
    service.verify_csrf(refreshed, csrf_token)
    with pytest.raises(AuthenticationError, match="csrf"):
        service.verify_csrf(refreshed, None)
    with pytest.raises(AuthenticationError, match="csrf"):
        service.verify_csrf(refreshed, "wrong")
    await service.logout(refreshed, now=NOW)
    assert sessions.state.revoked_at == NOW


async def test_csrf_rotation_fails_if_session_disappears() -> None:
    service, sessions, _, token = build_service()
    user = await service.authenticate(token, now=NOW)
    sessions.allow_update = False
    with pytest.raises(AuthenticationError):
        await service.issue_csrf(user, now=NOW)


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        ((), {"report": False, "review": False, "issue_report": False}),
        (
            (RoleScope("unit_coordinator", 1),),
            {"report": True, "review": False, "issue_report": False},
        ),
        (
            (RoleScope("leader", None),),
            {"report": False, "review": False, "issue_report": True},
        ),
        (
            (RoleScope("supervision_admin", None),),
            {"report": True, "review": True, "issue_report": True},
        ),
    ],
)
def test_capabilities_are_derived_from_roles(
    roles: tuple[RoleScope, ...],
    expected: dict[str, bool],
) -> None:
    assert capabilities(roles) == expected


@pytest.mark.parametrize(
    "value",
    [NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=8)))],
)
async def test_service_requires_utc(value: datetime) -> None:
    service, _, _, token = build_service()
    with pytest.raises(ValueError, match="UTC"):
        await service.authenticate(token, now=value)
