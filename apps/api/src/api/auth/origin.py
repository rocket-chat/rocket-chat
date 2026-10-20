"""The public address of the web app, for URLs a browser or a provider must reach."""

from __future__ import annotations

import os

from fastapi import Request

# Set by the Helm chart (ROCKET_PUBLIC_URL, derived like NEXTAUTH_URL) or by the operator.
_ORIGIN_VARIABLES = ("ROCKET_PUBLIC_URL", "NEXTAUTH_URL", "APP_URL", "FRONTEND_URL")


def public_origin(request: Request) -> str:
    """Scheme and host people reach the app on, never the backend's internal address.

    The web app proxies ``/v1`` to the backend, so ``request.base_url`` is the backend's
    service address: an OAuth callback built from it sends the browser somewhere it cannot
    reach. The configured address wins, then the proxy's forwarded host.
    """
    for name in _ORIGIN_VARIABLES:
        if explicit := (os.getenv(name) or "").strip():
            return explicit.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if host:
        return f"{proto}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def public_base_url() -> str:
    """The configured public address, for text written without a request (prompts)."""
    for name in _ORIGIN_VARIABLES:
        if explicit := (os.getenv(name) or "").strip():
            return explicit.rstrip("/")
    return ""


def mcp_oauth_callback_url(request: Request) -> str:
    """Where an MCP server's authorization server sends the browser back."""
    return f"{public_origin(request)}/v1/mcp/oauth/callback"
