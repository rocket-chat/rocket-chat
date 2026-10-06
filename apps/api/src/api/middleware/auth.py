"""Authentication middleware and dependency injecting RequestContext into FastAPI endpoints."""

import logging
import os
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api.auth.context import RequestContext
from api.auth.jwt_validator import JWTValidationError, JWTValidator

logger = logging.getLogger(__name__)

EXEMPT_PREFIXES = (
    "/health",
    "/api/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/webhooks",
)


class AuthMiddleware(BaseHTTPMiddleware):
    """Intercepts HTTP requests, validates Bearer JWT tokens, and attaches RequestContext to request.state."""

    def __init__(
        self,
        app: Any,
        validator: JWTValidator | None = None,
        exempt_paths: tuple[str, ...] = EXEMPT_PREFIXES,
    ) -> None:
        super().__init__(app)
        self.validator = validator or JWTValidator()
        self.exempt_paths = exempt_paths

    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Response:
        path = request.url.path

        # Allow exempt public endpoints to pass through without authentication
        if any(path.startswith(prefix) for prefix in self.exempt_paths):
            return await call_next(request)  # type: ignore[no-any-return]

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            # Check development bypass
            if os.getenv("DEV_AUTH_BYPASS", "").lower() in ("true", "1", "yes"):
                request.state.context = RequestContext(
                    user_id=os.getenv("DEV_USER_ID", "dev-user-001"),
                    org_id=os.getenv("DEV_ORG_ID", "dev-org-alpha"),
                    email="dev@rocket-chat.internal",
                )
                return await call_next(request)  # type: ignore[no-any-return]

            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing Bearer token in Authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header[7:].strip()
        try:
            context = self.validator.validate_token(token)
            request.state.context = context
        except JWTValidationError as err:
            logger.warning("Authentication failure on %s: %s", path, err)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": str(err)},
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)  # type: ignore[no-any-return]


def get_current_context(request: Request) -> RequestContext:
    """Dependency extracting the authenticated RequestContext from the active request state."""
    ctx = getattr(request.state, "context", None)
    if isinstance(ctx, RequestContext):
        return ctx

    # Check for direct Authorization header if middleware wasn't executed
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        validator = JWTValidator()
        try:
            return validator.validate_token(token)
        except JWTValidationError as err:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(err),
            ) from err

    if os.getenv("DEV_AUTH_BYPASS", "").lower() in ("true", "1", "yes"):
        return RequestContext(
            user_id="dev-user-001",
            org_id="dev-org-alpha",
            email="dev@rocket-chat.internal",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized: active RequestContext not found",
    )
