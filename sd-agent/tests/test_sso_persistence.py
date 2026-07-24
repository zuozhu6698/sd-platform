from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from sd_agent.persistence import sso as sso_module
from sd_agent.persistence.models import AuditEvent, AuthSession, SsoLoginAttempt
from sd_agent.persistence.sso import SqlSsoPersistence
from sd_agent.sso import PendingSsoAttempt

NOW = datetime(2026, 7, 24, 5, 0, tzinfo=UTC)


class FakeSession:
    def __init__(self, scalars: list[object | None] | None = None) -> None:
        self.values = list(scalars or [])
        self.added: list[object] = []

    async def scalar(self, _statement: object) -> object | None:
        return self.values.pop(0) if self.values else None

    def add(self, value: object) -> None:
        self.added.append(value)


class FakeSessions:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeSession]:
        yield self.session

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[FakeSession]:
        yield self.session


def persistence(session: FakeSession) -> SqlSsoPersistence:
    value = object.__new__(SqlSsoPersistence)
    value._sessions = FakeSessions(session)  # type: ignore[assignment]
    return value


def attempt() -> PendingSsoAttempt:
    return PendingSsoAttempt("a" * 64, "b" * 64, "/home", NOW, NOW + timedelta(minutes=5))


def test_sso_persistence_builds_non_expiring_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[object, bool]] = []

    def factory(engine: object, *, expire_on_commit: bool) -> object:
        seen.append((engine, expire_on_commit))
        return object()

    monkeypatch.setattr(sso_module, "async_sessionmaker", factory)
    engine = object()
    SqlSsoPersistence(engine)  # type: ignore[arg-type]
    assert seen == [(engine, False)]


async def test_create_and_read_pending_attempt() -> None:
    session = FakeSession()
    repository = persistence(session)
    await repository.create_attempt(attempt())
    row = next(value for value in session.added if isinstance(value, SsoLoginAttempt))
    assert row.state_hash == "a" * 64 and row.ticket_hash is None

    session.values = [row]
    loaded = await repository.get_pending_attempt("a" * 64, now=NOW)
    assert loaded == attempt()


async def test_consume_creates_session_and_success_audit() -> None:
    row = SsoLoginAttempt(
        attempt_id="id",
        state_hash="a" * 64,
        nonce_hash="b" * 64,
        redirect_path="/home",
        ticket_hash=None,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        consumed_at=None,
    )
    session = FakeSession([None, None, row])
    redirect = await persistence(session).consume_and_create_session(
        state_hash=row.state_hash,
        ticket_hash="c" * 64,
        person_id=7,
        sid="sid",
        kid="v1",
        expires_at=NOW + timedelta(hours=1),
        request_id="req-1",
        now=NOW,
    )
    assert redirect == "/home"
    assert row.ticket_hash == "c" * 64 and row.consumed_at == NOW
    assert any(isinstance(value, AuthSession) for value in session.added)
    audit = next(value for value in session.added if isinstance(value, AuditEvent))
    assert (audit.what, audit.result, audit.who) == ("auth.sso.login", "success", "7")


async def test_duplicate_ticket_and_invalid_state_do_not_create_session() -> None:
    duplicate = FakeSession([None, "existing"])
    result = await persistence(duplicate).consume_and_create_session(
        state_hash="a" * 64,
        ticket_hash="c" * 64,
        person_id=7,
        sid="sid",
        kid="v1",
        expires_at=NOW + timedelta(hours=1),
        request_id="req",
        now=NOW,
    )
    assert result is None and duplicate.added == []

    missing = FakeSession([None, None, None])
    result = await persistence(missing).consume_and_create_session(
        state_hash="a" * 64,
        ticket_hash="d" * 64,
        person_id=7,
        sid="sid-2",
        kid="v1",
        expires_at=NOW + timedelta(hours=1),
        request_id="req",
        now=NOW,
    )
    assert result is None and missing.added == []


async def test_failure_audit_contains_only_hash_prefix_and_error_code() -> None:
    row = SsoLoginAttempt(
        attempt_id="id",
        state_hash="a" * 64,
        nonce_hash="b" * 64,
        redirect_path="/home",
        ticket_hash=None,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        consumed_at=None,
    )
    session = FakeSession([row])
    await persistence(session).record_failure(
        state_hash="a" * 64,
        request_id="req-2",
        error_code="SSO_NONCE_INVALID",
        now=NOW,
    )
    audit = next(value for value in session.added if isinstance(value, AuditEvent))
    assert audit.target_id == "a" * 16
    assert audit.details == {"error_code": "SSO_NONCE_INVALID", "provider": "oa"}
    assert audit.result == "failed" and audit.who == "anonymous"
    assert row.consumed_at == NOW

    unknown = FakeSession([None])
    await persistence(unknown).record_failure(
        state_hash="f" * 64,
        request_id="req-3",
        error_code="SSO_STATE_INVALID",
        now=NOW,
    )
    unknown_audit = next(value for value in unknown.added if isinstance(value, AuditEvent))
    assert unknown_audit.target_id == "f" * 16
