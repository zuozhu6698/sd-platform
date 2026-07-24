from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from sd_agent.auth import TokenService
from sd_agent.auth.service import PersonIdentity
from sd_agent.sso.service import (
    MockSsoProvider,
    PendingSsoAttempt,
    SsoError,
    SsoService,
)

NOW = datetime(2026, 7, 24, 5, 0, tzinfo=UTC)


class FakePersistence:
    def __init__(self) -> None:
        self.attempts: dict[str, PendingSsoAttempt] = {}
        self.sessions: dict[str, object] = {}
        self.ticket_hashes: set[str] = set()
        self.failures: list[dict[str, object]] = []

    async def create_attempt(self, attempt: PendingSsoAttempt) -> None:
        self.attempts[attempt.state_hash] = attempt

    async def get_pending_attempt(
        self,
        state_hash: str,
        *,
        now: datetime,
    ) -> PendingSsoAttempt | None:
        attempt = self.attempts.get(state_hash)
        if attempt is None or attempt.consumed_at is not None or attempt.expires_at <= now:
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
        if attempt is None or ticket_hash in self.ticket_hashes:
            return None
        self.ticket_hashes.add(ticket_hash)
        self.attempts[state_hash] = replace(attempt, consumed_at=now)
        self.sessions[sid] = {
            "person_id": person_id,
            "kid": kid,
            "expires_at": expires_at,
            "request_id": request_id,
        }
        return attempt.redirect_path

    async def record_failure(
        self,
        *,
        state_hash: str,
        request_id: str,
        error_code: str,
        now: datetime,
    ) -> None:
        attempt = self.attempts.get(state_hash)
        if attempt is not None and attempt.consumed_at is None and attempt.expires_at > now:
            self.attempts[state_hash] = replace(attempt, consumed_at=now)
        self.failures.append(
            {
                "state_hash": state_hash,
                "request_id": request_id,
                "error_code": error_code,
                "now": now,
            }
        )


class FakeIdentities:
    def __init__(self, person: PersonIdentity | None = None) -> None:
        self.person = person or PersonIdentity(7, "张三", 10, True, 1)

    async def get_person(self, person_id: int) -> PersonIdentity | None:
        return self.person if self.person and self.person.person_id == person_id else None


def service(
    *,
    persistence: FakePersistence | None = None,
    identities: FakeIdentities | None = None,
    provider: MockSsoProvider | None = None,
) -> tuple[SsoService, FakePersistence, MockSsoProvider]:
    store = persistence or FakePersistence()
    mock = provider or MockSsoProvider(person_id=7)
    return (
        SsoService(
            provider=mock,
            persistence=store,
            identities=identities or FakeIdentities(),
            tokens=TokenService(active_kid="v1", keys={"v1": "j" * 32}, expire_minutes=60),
            allowed_redirect_paths=("/", "/home", "/report"),
        ),
        store,
        mock,
    )


class NonStubProvider:
    def authorization_url(self, state: str, nonce: str) -> str:
        return "/external"

    def exchange_ticket(self, ticket: str) -> object:
        raise AssertionError("not used")


async def authorize_flow(
    value: SsoService,
    provider: MockSsoProvider,
    *,
    redirect: str = "/home",
) -> tuple[str, str]:
    started = await value.start(redirect, now=NOW)
    ticket = provider.authorize(started.state, started.nonce)
    return started.state, ticket


async def test_start_hashes_state_and_nonce_and_validates_redirect() -> None:
    sso, store, _ = service()
    started = await sso.start("/home", now=NOW)
    assert started.authorization_url.startswith("/api/sso/stub/authorize?")
    assert started.state not in str(store.attempts)
    attempt = next(iter(store.attempts.values()))
    assert attempt.redirect_path == "/home"
    assert len(attempt.state_hash) == len(attempt.nonce_hash) == 64
    with pytest.raises(SsoError, match="SSO_REDIRECT_INVALID"):
        await sso.start("https://evil.test", now=NOW)


async def test_callback_creates_session_and_returns_internal_redirect() -> None:
    sso, store, provider = service()
    state, ticket = await authorize_flow(sso, provider)
    result = await sso.callback(ticket=ticket, state=state, request_id="req-1", now=NOW)
    principal = sso.verify_issued_token(result.token, now=NOW)
    assert result.redirect_path == "/home"
    assert principal.person_id == 7
    assert principal.sid in store.sessions


async def test_consumed_state_is_rejected_before_ticket_exchange() -> None:
    sso, _, provider = service()
    state, ticket = await authorize_flow(sso, provider)
    await sso.callback(ticket=ticket, state=state, request_id="req-1", now=NOW)
    with pytest.raises(SsoError, match="SSO_STATE_INVALID"):
        await sso.callback(ticket=ticket, state=state, request_id="req-2", now=NOW)


async def test_callback_rejects_unknown_expired_and_nonce_mismatch() -> None:
    sso, store, provider = service()
    with pytest.raises(SsoError, match="SSO_STATE_INVALID"):
        await sso.callback(ticket="ticket", state="unknown", request_id="req", now=NOW)

    started = await sso.start("/home", now=NOW)
    expired_ticket = provider.authorize(started.state, started.nonce)
    with pytest.raises(SsoError, match="SSO_STATE_INVALID"):
        await sso.callback(
            ticket=expired_ticket,
            state=started.state,
            request_id="req",
            now=NOW + timedelta(minutes=6),
        )

    started = await sso.start("/home", now=NOW)
    wrong_nonce_ticket = provider.authorize(started.state, "wrong-nonce")
    with pytest.raises(SsoError, match="SSO_NONCE_INVALID"):
        await sso.callback(
            ticket=wrong_nonce_ticket,
            state=started.state,
            request_id="req",
            now=NOW,
        )
    assert [failure["error_code"] for failure in store.failures] == [
        "SSO_STATE_INVALID",
        "SSO_STATE_INVALID",
        "SSO_NONCE_INVALID",
    ]
    assert all(len(str(failure["state_hash"])) == 64 for failure in store.failures)


@pytest.mark.parametrize(
    "person",
    [None, PersonIdentity(7, "张三", 10, False, 1)],
)
async def test_callback_rejects_missing_or_inactive_person(
    person: PersonIdentity | None,
) -> None:
    identities = FakeIdentities()
    identities.person = person
    sso, store, provider = service(identities=identities)
    state, ticket = await authorize_flow(sso, provider)
    with pytest.raises(SsoError, match="SSO_PERSON_INACTIVE"):
        await sso.callback(ticket=ticket, state=state, request_id="req", now=NOW)
    assert store.sessions == {}


def test_mock_provider_rejects_tampered_and_replayed_tickets() -> None:
    provider = MockSsoProvider(person_id=7)
    ticket = provider.authorize("state", "nonce")
    identity = provider.exchange_ticket(ticket)
    assert (identity.person_id, identity.nonce) == (7, "nonce")
    with pytest.raises(SsoError, match="SSO_TICKET_INVALID"):
        provider.exchange_ticket(ticket)
    with pytest.raises(SsoError, match="SSO_TICKET_INVALID"):
        provider.exchange_ticket("unknown")


@pytest.mark.parametrize("attempt_minutes", [0, 16])
def test_service_rejects_unsafe_attempt_lifetime(attempt_minutes: int) -> None:
    with pytest.raises(ValueError, match="attempt lifetime"):
        SsoService(
            provider=MockSsoProvider(person_id=7),
            persistence=FakePersistence(),
            identities=FakeIdentities(),
            tokens=TokenService(active_kid="v1", keys={"v1": "j" * 32}, expire_minutes=60),
            allowed_redirect_paths=("/",),
            attempt_minutes=attempt_minutes,
        )


async def test_callback_rejects_malformed_ticket_and_consume_race() -> None:
    sso, store, provider = service()
    with pytest.raises(SsoError, match="SSO_TICKET_INVALID"):
        await sso.callback(ticket="", state="state", request_id="req", now=NOW)

    state, ticket = await authorize_flow(sso, provider)
    store.ticket_hashes.add("occupied")

    async def reject_consume(**_kwargs: object) -> None:
        return None

    store.consume_and_create_session = reject_consume  # type: ignore[method-assign,assignment]
    with pytest.raises(SsoError, match="SSO_STATE_INVALID"):
        await sso.callback(ticket=ticket, state=state, request_id="req", now=NOW)
    assert next(iter(store.attempts.values())).consumed_at == NOW


def test_stub_boundaries_and_utc_requirement() -> None:
    with pytest.raises(ValueError, match="person_id"):
        MockSsoProvider(person_id=0)
    provider = MockSsoProvider(person_id=7)
    with pytest.raises(SsoError, match="SSO_STATE_INVALID"):
        provider.authorize("", "nonce")

    sso, _, _ = service()
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        sso.verify_issued_token("token", now=datetime(2026, 7, 24, 5, 0))

    non_stub = SsoService(
        provider=NonStubProvider(),  # type: ignore[arg-type]
        persistence=FakePersistence(),
        identities=FakeIdentities(),
        tokens=TokenService(active_kid="v1", keys={"v1": "j" * 32}, expire_minutes=60),
        allowed_redirect_paths=("/",),
    )
    with pytest.raises(SsoError, match="SSO_STUB_DISABLED"):
        non_stub.authorize_stub("state", "nonce")
