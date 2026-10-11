"""Authentication and authorization utilities."""

from .context import RequestContext
from .jwt_validator import JWTValidationError, JWTValidator
from .nextauth_cookie import decode_nextauth_token, extract_nextauth_context
from .session_manager import (
    SESSION_COOKIE_NAME,
    clear_session_cookie,
    create_session_token,
    extract_session_context,
    set_session_cookie,
    verify_session_token,
)

__all__ = [
    "SESSION_COOKIE_NAME",
    "JWTValidationError",
    "JWTValidator",
    "RequestContext",
    "clear_session_cookie",
    "create_session_token",
    "decode_nextauth_token",
    "extract_nextauth_context",
    "extract_session_context",
    "set_session_cookie",
    "verify_session_token",
]
