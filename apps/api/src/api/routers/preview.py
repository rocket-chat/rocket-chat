"""Live preview of an app running inside a session's sandbox.

``POST /v1/sessions/{id}/preview`` (authenticated) mints a link for one port. The link is
served by ``/v1/preview/<token>/...``, which needs no cookie: the token is the capability. The
content is the agent's own output and therefore untrusted, so every response is sent with a
``sandbox`` content-security-policy (an opaque origin, no access to the cockpit's cookies or
storage) and the cockpit embeds it in a sandboxed iframe without ``allow-same-origin``.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sandbox_driver.loopback_http import (
    MAX_PORT,
    MIN_PORT,
    LoopbackError,
    request_loopback,
)

from api.auth import preview_token
from api.auth.access import _check_session_access, _get_request_identity
from api.db.session import current_tenant_org_id
from api.services.preview_rewrite import rewrite_css, rewrite_html, rewrite_location
from specifications.interfaces.sandbox import SandboxDriverProtocol, SandboxStatus
from specifications.interfaces.session import SessionStoreProtocol

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Preview"])

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BODY = 64 * 1024
# Each proxied request runs commands in the sandbox; bound how many run at once per process.
_gate = asyncio.Semaphore(8)

_FORWARDED_REQUEST_HEADERS = ("accept", "accept-language", "content-type")
_FORWARDED_RESPONSE_HEADERS = ("content-type", "cache-control", "etag", "last-modified", "location")
_SANDBOX_CSP = "sandbox allow-scripts allow-forms allow-popups allow-modals"


class PreviewRequest(BaseModel):
    port: int = Field(..., ge=MIN_PORT, le=MAX_PORT, description="A port the app listens on")


class PreviewLink(BaseModel):
    url: str = Field(..., description="Relative URL to embed; valid until expires_at")
    port: int
    expires_at: int


@router.post("/sessions/{session_id}/preview", response_model=PreviewLink)
async def create_preview_link(
    session_id: str, body: PreviewRequest, request: Request
) -> PreviewLink:
    org_id, user_id, roles = _get_request_identity(request)
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(record, user_id, roles, require_write=False)
    token, expires_at = preview_token.mint(session_id, org_id, user_id, body.port)
    return PreviewLink(url=f"/v1/preview/{token}/", port=body.port, expires_at=expires_at)


@router.api_route(
    "/preview/{token}/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def serve_preview(token: str, path: str, request: Request) -> Response:
    try:
        grant = preview_token.verify(token)
    except ValueError as err:
        raise HTTPException(status_code=401, detail=str(err)) from err

    driver: SandboxDriverProtocol | None = getattr(request.app.state, "sandbox_driver", None)
    if driver is None or await driver.get_status(grant.session_id) != SandboxStatus.RUNNING:
        raise HTTPException(
            status_code=409, detail="The sandbox is not running; send a message to start it."
        )
    store: SessionStoreProtocol = request.app.state.session_store
    tenant = current_tenant_org_id.set(grant.org_id)
    try:
        if await store.get_session(grant.session_id) is None:
            raise HTTPException(status_code=404, detail="Session not found")
    finally:
        current_tenant_org_id.reset(tenant)

    body = await request.body()
    if len(body) > MAX_REQUEST_BODY:
        raise HTTPException(status_code=413, detail="Request body too large")
    target = "/" + path + (f"?{request.url.query}" if request.url.query else "")
    forwarded = {
        name: request.headers[name]
        for name in _FORWARDED_REQUEST_HEADERS
        if name in request.headers
    }
    try:
        async with _gate:
            upstream = await request_loopback(
                driver,
                grant.session_id,
                grant.port,
                target,
                method=request.method,
                headers=forwarded,
                body=body,
                max_response_bytes=MAX_RESPONSE_BYTES,
            )
    except LoopbackError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    except Exception as err:
        logger.warning("Preview request failed for %s: %s", grant.session_id, err)
        raise HTTPException(status_code=502, detail="The preview could not be fetched") from err

    prefix = f"/v1/preview/{token}/"
    content_type = upstream.headers.get("content-type", "application/octet-stream")
    payload = upstream.body
    media = content_type.split(";", 1)[0].strip().lower()
    if media in ("text/html", "application/xhtml+xml"):
        payload = rewrite_html(payload.decode("utf-8", errors="replace"), prefix).encode()
    elif media == "text/css":
        payload = rewrite_css(payload.decode("utf-8", errors="replace"), prefix).encode()

    headers = {n: upstream.headers[n] for n in _FORWARDED_RESPONSE_HEADERS if n in upstream.headers}
    if "location" in headers:
        headers["location"] = rewrite_location(headers["location"], prefix, grant.port)
    headers.update(
        {
            "content-security-policy": _SANDBOX_CSP,
            "x-content-type-options": "nosniff",
            "referrer-policy": "no-referrer",
            "cache-control": "no-store",
        }
    )
    return Response(
        content=payload if request.method != "HEAD" else b"",
        status_code=upstream.status,
        headers=headers,
    )
