"""Authentication primitives with no transport or persistence coupling."""

from sd_agent.auth.security import (
    AuthTokenError,
    CsrfProtector,
    Principal,
    TokenService,
    validate_redirect_path,
)
from sd_agent.auth.service import (
    AuthenticatedUser,
    AuthenticationError,
    AuthService,
    PersonIdentity,
    RoleScope,
    SessionState,
    capabilities,
)

__all__ = [
    "AuthTokenError",
    "AuthenticatedUser",
    "AuthenticationError",
    "AuthService",
    "CsrfProtector",
    "Principal",
    "PersonIdentity",
    "RoleScope",
    "SessionState",
    "TokenService",
    "capabilities",
    "validate_redirect_path",
]
