from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI

from sd_agent.api_main import create_app
from sd_agent.auth import TokenService
from sd_agent.auth.service import PersonIdentity
from sd_agent.config import Settings
from sd_agent.sso import MockSsoProvider, PendingSsoAttempt, SsoService


class MemorySsoPersistence:
    def __init__(self) -> None:
        self.attempts: dict[str, PendingSsoAttempt] = {}
        self.consumed: set[str] = set()
        self.failures: list[str] = []

    async def create_attempt(self, attempt: PendingSsoAttempt) -> None:
        self.attempts[attempt.state_hash] = attempt

    async def get_pending_attempt(
        self, state_hash: str, *, now: datetime
    ) -> PendingSsoAttempt | None:
        attempt = self.attempts.get(state_hash)
        if attempt is None or state_hash in self.consumed or attempt.expires_at <= now:
            return None
        return attempt

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
        attempt = await self.get_pending_attempt(state_hash, now=now)
        if attempt is None:
            return None
        self.consumed.add(state_hash)
        return attempt.redirect_path

    async def record_failure(
        self,
        *,
        state_hash: str,
        request_id: str,
        error_code: str,
        now: datetime,
    ) -> None:
        self.failures.append(error_code)


class IdentityStore:
    async def get_person(self, person_id: int) -> PersonIdentity | None:
        return PersonIdentity(person_id, "测试用户", 10, True, 1)


@pytest.fixture
async def sso_client() -> AsyncIterator[tuple[httpx.AsyncClient, MemorySsoPersistence, FastAPI]]:
    settings = Settings(
        _env_file=None,
        ENV="test",
        COOKIE_SECURE=True,
        ALLOWED_REDIRECT_PATHS=("/", "/home"),
    )
    app = create_app(settings)
    store = MemorySsoPersistence()
    service = SsoService(
        provider=MockSsoProvider(person_id=7),
        persistence=store,
        identities=IdentityStore(),
        tokens=TokenService(active_kid="v1", keys={"v1": "j" * 32}, expire_minutes=60),
        allowed_redirect_paths=settings.ALLOWED_REDIRECT_PATHS,
    )
    async with app.router.lifespan_context(app):
        app.state.resources.sso = service
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
            follow_redirects=False,
        ) as client:
            yield client, store, app


async def test_sso_stub_completes_redirect_and_sets_secure_cookie(
    sso_client: tuple[httpx.AsyncClient, MemorySsoPersistence, FastAPI],
) -> None:
    client, _, _ = sso_client
    started = await client.get("/api/sso/oa/start", params={"redirect": "/home"})
    assert started.status_code == 302
    state_cookie = started.headers["set-cookie"]
    assert "sd_sso_state=" in state_cookie and "Max-Age=300" in state_cookie
    assert "HttpOnly" in state_cookie and "Secure" in state_cookie
    authorized = await client.get(started.headers["location"])
    assert authorized.status_code == 302
    completed = await client.get(authorized.headers["location"])
    assert completed.status_code == 302
    assert completed.headers["location"] == "/home"
    cookie = completed.headers["set-cookie"]
    assert "sd_token=" in cookie
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert 'sd_sso_state=""' in cookie

    replayed = await client.get(authorized.headers["location"])
    assert replayed.status_code == 401
    assert replayed.json()["error"]["code"] == "SSO_STATE_INVALID"


async def test_callback_requires_matching_short_lived_state_cookie(
    sso_client: tuple[httpx.AsyncClient, MemorySsoPersistence, FastAPI],
) -> None:
    client, _, _ = sso_client
    started = await client.get("/api/sso/oa/start", params={"redirect": "/home"})
    authorized = await client.get(started.headers["location"])
    client.cookies.set("sd_sso_state", "tampered", path="/api/sso/oa/callback")
    rejected = await client.get(authorized.headers["location"])
    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "SSO_STATE_INVALID"


async def test_callback_maps_provider_ticket_failure_without_echoing_ticket(
    sso_client: tuple[httpx.AsyncClient, MemorySsoPersistence, FastAPI],
) -> None:
    client, _, _ = sso_client
    started = await client.get("/api/sso/oa/start", params={"redirect": "/home"})
    authorized = await client.get(started.headers["location"])
    state = parse_qs(urlsplit(authorized.headers["location"]).query)["state"][0]
    rejected = await client.get(
        "/api/sso/oa/callback",
        params={"ticket": "tampered-ticket", "state": state},
    )
    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "SSO_TICKET_INVALID"
    assert "tampered-ticket" not in rejected.text


async def test_sso_rejects_external_redirect_and_unavailable_service(
    sso_client: tuple[httpx.AsyncClient, MemorySsoPersistence, FastAPI],
) -> None:
    client, _, app = sso_client
    rejected = await client.get("/api/sso/oa/start", params={"redirect": "https://evil.example"})
    assert rejected.status_code == 400
    assert "evil.example" not in rejected.text

    app.state.resources.sso = None
    unavailable = await client.get("/api/sso/oa/start")
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "SSO_UNAVAILABLE"


async def test_stub_authorize_is_hidden_when_provider_is_not_mock(
    sso_client: tuple[httpx.AsyncClient, MemorySsoPersistence, FastAPI],
) -> None:
    client, store, app = sso_client

    class ExternalProvider:
        def authorization_url(self, state: str, nonce: str) -> str:
            return "https://oa.example/authorize"

        def exchange_ticket(self, ticket: str) -> object:
            raise AssertionError("not used")

    app.state.resources.sso = SsoService(
        provider=ExternalProvider(),  # type: ignore[arg-type]
        persistence=store,
        identities=IdentityStore(),
        tokens=TokenService(active_kid="v1", keys={"v1": "j" * 32}, expire_minutes=60),
        allowed_redirect_paths=("/", "/home"),
    )
    response = await client.get(
        "/api/sso/stub/authorize", params={"state": "state", "nonce": "nonce"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SSO_STUB_DISABLED"
