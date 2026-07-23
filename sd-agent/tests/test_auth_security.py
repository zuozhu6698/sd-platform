from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import jwt
import pytest

from sd_agent.auth import (
    AuthTokenError,
    CsrfProtector,
    TokenService,
    validate_redirect_path,
)

NOW = datetime(2026, 7, 23, 1, 2, 3, tzinfo=UTC)
KEY = "k" * 32


def service() -> TokenService:
    return TokenService(active_kid="v1", keys={"v1": KEY}, expire_minutes=60)


def test_token_round_trip_has_only_the_contract_claims() -> None:
    token = service().issue(person_id=7, sid="sid_1", now=NOW)
    assert set(jwt.decode(token, options={"verify_signature": False})) == {
        "sub",
        "sid",
        "kid",
        "iat",
        "exp",
    }
    principal = service().verify(token, now=NOW + timedelta(minutes=1))
    assert principal.person_id == 7
    assert principal.sid == "sid_1"
    assert principal.kid == "v1"
    assert principal.expires_at == NOW + timedelta(minutes=60)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"active_kid": "missing", "keys": {"v1": KEY}, "expire_minutes": 60},
        {"active_kid": "v1", "keys": {"v1": "short"}, "expire_minutes": 60},
        {"active_kid": "v1", "keys": {"v1": KEY}, "expire_minutes": 4},
        {"active_kid": "v1", "keys": {"v1": KEY}, "expire_minutes": 1441},
    ],
)
def test_token_service_rejects_unsafe_configuration(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        TokenService(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=8)))],
)
def test_token_service_requires_utc(value: datetime) -> None:
    with pytest.raises(ValueError, match="UTC"):
        service().issue(person_id=1, sid="sid", now=value)


def test_token_rejects_unknown_kid() -> None:
    token = jwt.encode(
        {"sub": "1", "sid": "s", "kid": "v2", "iat": NOW, "exp": NOW + timedelta(hours=1)},
        "x" * 32,
        algorithm="HS256",
        headers={"kid": "v2"},
    )
    with pytest.raises(AuthTokenError):
        service().verify(token, now=NOW)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda claims: {**claims, "scope": "admin"},
        lambda claims: {**claims, "kid": "v2"},
        lambda claims: {**claims, "sub": "0"},
        lambda claims: {**claims, "sub": "not-an-id"},
        lambda claims: {**claims, "sid": ""},
        lambda claims: {**claims, "iat": int((NOW + timedelta(minutes=1)).timestamp())},
        lambda claims: {**claims, "exp": int((NOW - timedelta(seconds=1)).timestamp())},
    ],
)
def test_token_rejects_invalid_claim_contract(mutate: object) -> None:
    base = {
        "sub": "1",
        "sid": "s",
        "kid": "v1",
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + timedelta(hours=1)).timestamp()),
    }
    claims = mutate(base)  # type: ignore[operator]
    token = jwt.encode(claims, KEY, algorithm="HS256", headers={"kid": "v1"})
    with pytest.raises(AuthTokenError):
        service().verify(token, now=NOW)


def test_token_rejects_bad_signature_and_malformed_payload() -> None:
    wrong = jwt.encode(
        {"sub": "1", "sid": "s", "kid": "v1", "iat": NOW, "exp": NOW + timedelta(hours=1)},
        "z" * 32,
        algorithm="HS256",
        headers={"kid": "v1"},
    )
    for token in (wrong, "not-a-jwt"):
        with pytest.raises(AuthTokenError):
            service().verify(token, now=NOW)


def test_csrf_round_trip_is_bound_to_session() -> None:
    protector = CsrfProtector("c" * 32)
    token, digest = protector.issue("sid_1")
    assert token not in digest
    assert protector.verify(sid="sid_1", token=token, expected_digest=digest)
    assert not protector.verify(sid="sid_2", token=token, expected_digest=digest)
    assert not protector.verify(sid="sid_1", token=f"{token}x", expected_digest=digest)


def test_csrf_rejects_short_secret() -> None:
    with pytest.raises(ValueError):
        CsrfProtector("short")


@pytest.mark.parametrize("path", ["/", "/home", "/m/report"])
def test_redirect_accepts_exact_allowlist(path: str) -> None:
    assert validate_redirect_path(path, allowed_paths=("/", "/home", "/m/report")) == path


@pytest.mark.parametrize(
    "path",
    [
        "",
        "home",
        "//evil.example/path",
        "https://evil.example",
        "/home?next=evil",
        "/home#fragment",
        "/%2f%2fevil.example",
        "/home%00",
        "/home\\evil",
        "/admin",
        "/home\n",
    ],
)
def test_redirect_rejects_open_redirect_shapes(path: str) -> None:
    with pytest.raises(ValueError, match="redirect"):
        validate_redirect_path(path, allowed_paths=("/", "/home"))
