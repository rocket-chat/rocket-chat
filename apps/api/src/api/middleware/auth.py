"""Authentication middleware and dependency injecting RequestContext into FastAPI endpoints."""

import logging
from collections.abc import Callable
from typing import Any, cast

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api.auth.context import RequestContext
from api.auth.identity import resolve_identity
from api.auth.jwt_validator import JWTValidator
from api.auth.roles import with_live_roles
from api.services.metrics import record_auth_failure

logger = logging.getLogger(__name__)

EXEMPT_PREFIXES = (
    "/health",
    "/v1/health",
    "/api/health",
    "/api/auth",
    "/docs",
    "/openapi.json",
    "/redoc",
    # Signed by their own shared secret (HMAC), not by a user session.
    "/webhooks",
    "/v1/webhooks",
    "/api/v1/webhooks",
    # Unauthenticated operational surfaces: kubelet probes and Prometheus scrape
    # /metrics; they carry no user auth and must not be blocked by the identity gate.
    "/metrics",
    "/v1/ready",
    "/v1/drain",
    # A preview link is its own capability (a signed token in the path): the sandboxed iframe
    # that loads it has an opaque origin and sends no cookies.
    "/v1/preview/",
    # The git gateway authenticates sandbox git with the session's gateway capability header.
    "/v1/git/",
    # A public session link is its own capability: a signed, expiring, revocable token.
    "/v1/shared/",
)


class AuthMiddleware(BaseHTTPMiddleware):
    """Rejects anonymous HTTP requests and attaches the resolved RequestContext."""

    def __init__(
        self,
        app: Any,
        validator: JWTValidator | None = None,
        exempt_paths: tuple[str, ...] = EXEMPT_PREFIXES,
    ) -> None:
        super().__init__(app)
        self.validator = validator
        self.exempt_paths = exempt_paths

    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Response:
        path = request.url.path
        # CORS preflights carry no credentials by design.
        if request.method == "OPTIONS" or any(path.startswith(p) for p in self.exempt_paths):
            return cast(Response, await call_next(request))

        context = resolve_identity(request, self.validator)
        if context is None:
            _record_rejection(request)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Authentication required"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        request.state.context = await with_live_roles(
            context, getattr(request.app.state, "db_manager", None)
        )
        return cast(Response, await call_next(request))


def get_current_context(request: Request) -> RequestContext:
    """Dependency returning the authenticated RequestContext for the active request."""
    context = resolve_identity(request)
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return context


def _record_rejection(request: Request) -> None:
    """Count and log an anonymous rejection without ever recording the credential itself."""
    presented = bool(request.headers.get("authorization") or request.headers.get("cookie"))
    reason = "invalid_credentials" if presented else "missing_credentials"
    record_auth_failure(reason)
    client = request.client.host if request.client else "unknown"
    logger.warning(
        "Authentication failed: %s %s from %s (%s)",
        request.method,
        request.url.path,
        client,
        reason,
    )
