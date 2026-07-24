from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlencode

from sd_agent.auth.security import Principal, TokenService, validate_redirect_path
from sd_agent.auth.service import PersonIdentity


@dataclass(frozen=True, slots=True)
class PendingSsoAttempt:
    state_hash: str
    nonce_hash: str
    redirect_path: str
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SsoExternalIdentity:
    person_id: int
    nonce: str


@dataclass(frozen=True, slots=True)
class SsoStartResult:
    state: str
    nonce: str
    authorization_url: str


@dataclass(frozen=True, slots=True)
class SsoCallbackResult:
    token: str
    redirect_path: str
    expires_at: datetime


class SsoError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SsoProvider(Protocol):
    def authorization_url(self, state: str, nonce: str) -> str: ...

    def exchange_ticket(self, ticket: str) -> SsoExternalIdentity: ...


class SsoPersistence(Protocol):
    async def create_attempt(self, attempt: PendingSsoAttempt) -> None: ...

    async def get_pending_attempt(
        self,
        state_hash: str,
        *,
        now: datetime,
    ) -> PendingSsoAttempt | None: ...

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
    ) -> str | None: ...

    async def record_failure(
        self,
        *,
        state_hash: str,
        request_id: str,
        error_code: str,
        now: datetime,
    ) -> None: ...


class SsoIdentityStore(Protocol):
    async def get_person(self, person_id: int) -> PersonIdentity | None: ...


class SsoService:
    def __init__(
        self,
        *,
        provider: SsoProvider,
        persistence: SsoPersistence,
        identities: SsoIdentityStore,
        tokens: TokenService,
        allowed_redirect_paths: tuple[str, ...],
        attempt_minutes: int = 5,
    ) -> None:
        if not 1 <= attempt_minutes <= 15:
            raise ValueError("SSO attempt lifetime must be between 1 and 15 minutes")
        self._provider = provider
        self._persistence = persistence
        self._identities = identities
        self._tokens = tokens
        self._allowed_redirect_paths = allowed_redirect_paths
        self._attempt_lifetime = timedelta(minutes=attempt_minutes)

    async def start(self, redirect: str, *, now: datetime) -> SsoStartResult:
        current_time = _require_utc(now)
        try:
            redirect_path = validate_redirect_path(
                redirect,
                allowed_paths=self._allowed_redirect_paths,
            )
        except ValueError as exc:
            raise SsoError("SSO_REDIRECT_INVALID") from exc
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        await self._persistence.create_attempt(
            PendingSsoAttempt(
                state_hash=_digest(state),
                nonce_hash=_digest(nonce),
                redirect_path=redirect_path,
                created_at=current_time,
                expires_at=current_time + self._attempt_lifetime,
            )
        )
        return SsoStartResult(
            state,
            nonce,
            self._provider.authorization_url(state, nonce),
        )

    async def callback(
        self,
        *,
        ticket: str,
        state: str,
        request_id: str,
        now: datetime,
    ) -> SsoCallbackResult:
        current_time = _require_utc(now)
        safe_state = state if len(state) <= 512 else state[:512]
        state_hash = _digest(safe_state)
        try:
            if not ticket or len(ticket) > 512 or not state or len(state) > 512:
                raise SsoError("SSO_TICKET_INVALID")
            attempt = await self._persistence.get_pending_attempt(state_hash, now=current_time)
            if attempt is None:
                raise SsoError("SSO_STATE_INVALID")
            identity = self._provider.exchange_ticket(ticket)
            if not hmac.compare_digest(attempt.nonce_hash, _digest(identity.nonce)):
                raise SsoError("SSO_NONCE_INVALID")
            person = await self._identities.get_person(identity.person_id)
            if person is None or not person.active:
                raise SsoError("SSO_PERSON_INACTIVE")

            sid = secrets.token_urlsafe(32)
            token = self._tokens.issue(person_id=person.person_id, sid=sid, now=current_time)
            principal = self._tokens.verify(token, now=current_time)
            redirect_path = await self._persistence.consume_and_create_session(
                state_hash=state_hash,
                ticket_hash=_digest(ticket),
                person_id=person.person_id,
                sid=sid,
                kid=principal.kid,
                expires_at=principal.expires_at,
                request_id=request_id,
                now=current_time,
            )
            if redirect_path is None:
                raise SsoError("SSO_STATE_INVALID")
            return SsoCallbackResult(token, redirect_path, principal.expires_at)
        except SsoError as exc:
            await self._persistence.record_failure(
                state_hash=state_hash,
                request_id=request_id,
                error_code=exc.code,
                now=current_time,
            )
            raise

    def verify_issued_token(self, token: str, *, now: datetime) -> Principal:
        return self._tokens.verify(token, now=_require_utc(now))

    def authorize_stub(self, state: str, nonce: str) -> str:
        if not isinstance(self._provider, MockSsoProvider):
            raise SsoError("SSO_STUB_DISABLED")
        return self._provider.authorize(state, nonce)


class MockSsoProvider:
    def __init__(self, *, person_id: int) -> None:
        if person_id <= 0:
            raise ValueError("stub person_id must be positive")
        self._person_id = person_id
        self._tickets: dict[str, SsoExternalIdentity] = {}

    def authorization_url(self, state: str, nonce: str) -> str:
        query = urlencode({"state": state, "nonce": nonce})
        return f"/api/sso/stub/authorize?{query}"

    def authorize(self, state: str, nonce: str) -> str:
        if not state or not nonce:
            raise SsoError("SSO_STATE_INVALID")
        ticket = secrets.token_urlsafe(32)
        self._tickets[ticket] = SsoExternalIdentity(self._person_id, nonce)
        return ticket

    def exchange_ticket(self, ticket: str) -> SsoExternalIdentity:
        identity = self._tickets.pop(ticket, None)
        if identity is None:
            raise SsoError("SSO_TICKET_INVALID")
        return identity


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("datetime must be timezone-aware UTC")
    return value.astimezone(UTC)
