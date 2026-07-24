from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from sd_agent.api_main import create_app
from sd_agent.auth import CsrfProtector, TokenService
from sd_agent.auth.service import AuthService, PersonIdentity, RoleScope, SessionState
from sd_agent.config import Settings
from tests.test_auth_service import FakeIdentities, FakeSessions


@pytest.fixture
async def authenticated_client() -> AsyncIterator[tuple[httpx.AsyncClient, str, FakeSessions]]:
    now = datetime.now(UTC).replace(microsecond=0)
    tokens = TokenService(active_kid="v1", keys={"v1": "j" * 32}, expire_minutes=60)
    token = tokens.issue(person_id=7, sid="sid_api", now=now)
    sessions = FakeSessions(
        SessionState("sid_api", 7, "v1", "old", now + timedelta(minutes=60), None)
    )
    identities = FakeIdentities()
    identities.person = PersonIdentity(7, "张三", 10, True, 3)
    identities.roles = (
        RoleScope("domain_owner", 10),
        RoleScope("leader", None),
    )
    auth = AuthService(
        tokens=tokens,
        csrf=CsrfProtector("c" * 32),
        sessions=sessions,
        identities=identities,
    )
    app = create_app(Settings(_env_file=None, ENV="test"))
    async with app.router.lifespan_context(app):
        app.state.resources.auth = auth
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
        ) as client:
            yield client, token, sessions


async def test_me_rotates_csrf_and_returns_dynamic_capabilities(
    authenticated_client: tuple[httpx.AsyncClient, str, FakeSessions],
) -> None:
    client, token, sessions = authenticated_client
    client.cookies.set("sd_token", token)
    response = await client.get("/api/me")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["person"] == {"person_id": 7, "name": "张三", "unit_id": 10}
    assert data["can"] == {
        "report": True,
        "review": False,
        "issue_report": True,
        "outbox_view": False,
        "outbox_replay_approve": False,
        "outbox_replay_execute": False,
    }
    assert data["csrf_token"]
    assert sessions.state is not None
    assert sessions.state.csrf_hash != "old"


async def test_logout_requires_rotated_csrf_and_revokes_session(
    authenticated_client: tuple[httpx.AsyncClient, str, FakeSessions],
) -> None:
    client, token, sessions = authenticated_client
    client.cookies.set("sd_token", token)
    me_response = await client.get("/api/me")
    csrf_token = me_response.json()["data"]["csrf_token"]
    response = await client.post(
        "/api/logout",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"logged_out": True}
    assert "sd_token=" in response.headers["set-cookie"]
    assert sessions.state is not None and sessions.state.revoked_at is not None


async def test_logout_rejects_bad_csrf(
    authenticated_client: tuple[httpx.AsyncClient, str, FakeSessions],
) -> None:
    client, token, _ = authenticated_client
    client.cookies.set("sd_token", token)
    response = await client.post(
        "/api/logout",
        headers={"X-CSRF-Token": "wrong"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


async def test_auth_endpoints_reject_missing_or_bad_session(
    authenticated_client: tuple[httpx.AsyncClient, str, FakeSessions],
) -> None:
    client, _, _ = authenticated_client
    missing = await client.get("/api/me")
    client.cookies.set("sd_token", "bad")
    invalid = await client.get("/api/me")
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "AUTH_REQUIRED"
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "SESSION_INVALID"


async def test_auth_endpoint_is_honest_when_dependencies_are_unconfigured() -> None:
    app = create_app(Settings(_env_file=None, ENV="test"))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
        ) as client:
            client.cookies.set("sd_token", "opaque")
            response = await client.get("/api/me")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_UNAVAILABLE"
