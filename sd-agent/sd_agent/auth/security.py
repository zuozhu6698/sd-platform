from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urlsplit

import jwt


class AuthTokenError(ValueError):
    """Safe authentication failure without exposing cryptographic details."""


@dataclass(frozen=True, slots=True)
class Principal:
    person_id: int
    sid: str
    kid: str
    issued_at: datetime
    expires_at: datetime


class TokenService:
    def __init__(self, *, active_kid: str, keys: dict[str, str], expire_minutes: int) -> None:
        if active_kid not in keys:
            raise ValueError("active JWT kid has no key")
        if any(len(secret) < 32 for secret in keys.values()):
            raise ValueError("JWT keys must contain at least 32 characters")
        if not 5 <= expire_minutes <= 1440:
            raise ValueError("expire_minutes must be between 5 and 1440")
        self._active_kid = active_kid
        self._keys = keys.copy()
        self._expire_minutes = expire_minutes

    def issue(self, *, person_id: int, sid: str, now: datetime) -> str:
        issued_at = _require_utc(now)
        expires_at = issued_at + timedelta(minutes=self._expire_minutes)
        claims = {
            "sub": str(person_id),
            "sid": sid,
            "kid": self._active_kid,
            "iat": issued_at,
            "exp": expires_at,
        }
        return jwt.encode(
            claims,
            self._keys[self._active_kid],
            algorithm="HS256",
            headers={"kid": self._active_kid, "typ": "JWT"},
        )

    def verify(self, token: str, *, now: datetime) -> Principal:
        current_time = _require_utc(now)
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if not isinstance(kid, str) or kid not in self._keys:
                raise AuthTokenError("session token is invalid")
            claims = jwt.decode(
                token,
                self._keys[kid],
                algorithms=["HS256"],
                options={
                    "require": ["sub", "sid", "kid", "iat", "exp"],
                    "verify_exp": False,
                    "verify_iat": True,
                },
            )
        except AuthTokenError:
            raise
        except jwt.PyJWTError as exc:
            raise AuthTokenError("session token is invalid") from exc

        if set(claims) != {"sub", "sid", "kid", "iat", "exp"}:
            raise AuthTokenError("session token is invalid")
        if claims["kid"] != kid:
            raise AuthTokenError("session token is invalid")
        try:
            person_id = int(claims["sub"])
            sid = str(claims["sid"])
            issued_at = datetime.fromtimestamp(int(claims["iat"]), tz=UTC)
            expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=UTC)
        except (TypeError, ValueError, OverflowError) as exc:
            raise AuthTokenError("session token is invalid") from exc
        if person_id <= 0 or not sid or issued_at > current_time or expires_at <= current_time:
            raise AuthTokenError("session token is invalid")
        return Principal(person_id, sid, kid, issued_at, expires_at)


class CsrfProtector:
    def __init__(self, secret: str) -> None:
        if len(secret) < 32:
            raise ValueError("CSRF secret must contain at least 32 characters")
        self._secret = secret.encode()

    def issue(self, sid: str) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        return token, self.digest(sid=sid, token=token)

    def digest(self, *, sid: str, token: str) -> str:
        message = f"{sid}:{token}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def verify(self, *, sid: str, token: str, expected_digest: str) -> bool:
        actual = self.digest(sid=sid, token=token)
        return hmac.compare_digest(actual, expected_digest)


def validate_redirect_path(raw: str, *, allowed_paths: tuple[str, ...]) -> str:
    if not raw or any(ord(character) < 32 for character in raw):
        raise ValueError("redirect path is invalid")
    if "%" in raw or "\\" in raw:
        raise ValueError("redirect path is invalid")
    decoded = unquote(raw)
    parsed = urlsplit(decoded)
    if (
        decoded.startswith("//")
        or not decoded.startswith("/")
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("redirect path is invalid")
    if decoded not in allowed_paths:
        raise ValueError("redirect path is not allowed")
    return decoded


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("datetime must be timezone-aware UTC")
    return value.astimezone(UTC)
