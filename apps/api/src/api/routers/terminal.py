"""Interactive terminal into a session's sandbox, over a WebSocket.

Frames: binary client -> server is keystrokes, binary server -> client is terminal output; text
frames carry JSON control (`{"type":"resize","cols":..,"rows":..}`, `{"type":"ping"}`) and the
server's `{"type":"error"|"exit"|"pong"}`. The shell is the sandbox's own, with exactly the
agent's user, limits and network rules, so the terminal grants a member nothing the agent does
not already have - but it is still a shell, so it is gated like a write to the session: the
caller must be authenticated, hold write access to the session in their organization, not be
read-only, and the organization must not have disabled terminals. Opening and closing one is
audited. Terminal bytes are never stored.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections import defaultdict

import anyio
from fastapi import APIRouter, HTTPException, Query, WebSocket

from api.auth.access import _check_session_access
from api.auth.context import RequestContext
from api.auth.identity import (
    attach_api_token_identity,
    resolve_identity,
    resolve_token_identity,
)
from api.auth.roles import with_live_roles
from api.db.session import current_tenant_org_id
from api.services.audit import AuditService
from api.services.settings import effective_settings
from specifications.interfaces.sandbox import PtyChannel, SandboxStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Terminal"])

MAX_INPUT_CHUNK_BYTES = 64 * 1024
MAX_TERMINALS_PER_SESSION = 2
DEFAULT_IDLE_SECONDS = 900
# Per replica; a session's terminals all live on the replica holding their WebSocket.
_open_terminals: defaultdict[str, int] = defaultdict(int)


def _idle_limit_seconds() -> float:
    return float(os.getenv("ROCKET_TERMINAL_IDLE_SECONDS", DEFAULT_IDLE_SECONDS))


async def _refuse(websocket: WebSocket, code: str, detail: str) -> None:
    """Tell the client why, then close (after accept, so the browser can read the reason)."""
    await websocket.accept()
    await websocket.send_json({"type": "error", "code": code, "detail": detail})
    await websocket.close(code=1008)


async def _authorise(websocket: WebSocket, session_id: str, token: str) -> RequestContext | None:
    """The caller when they may write to this session; otherwise the socket is closed."""
    app_state = websocket.app.state
    await attach_api_token_identity(websocket, getattr(app_state, "db_manager", None), token)
    caller = resolve_identity(websocket) or resolve_token_identity(token)
    if caller is not None:
        caller = await with_live_roles(caller, getattr(app_state, "db_manager", None))
    if caller is None:
        await websocket.close(code=1008, reason="Unauthorized: authentication required")
        return None
    # The tenant middleware only covers HTTP; bind the organization for row-level security.
    current_tenant_org_id.set(caller.org_id)

    session = await app_state.session_store.get_session(session_id)
    if not session or session.tenant_org_id != caller.org_id:
        await websocket.close(code=1008, reason="Session not found")
        return None
    try:
        # Writing to a sandbox is a write to the session: owners, collaborators and admins.
        _check_session_access(session, caller.user_id, list(caller.roles), require_write=True)
    except HTTPException:
        await websocket.close(code=1008, reason="Forbidden: terminal needs write access")
        return None
    return caller


async def _open_shell(
    websocket: WebSocket, caller: RequestContext, session_id: str, cols: int, rows: int
) -> PtyChannel | None:
    """Open the sandbox shell, or tell the client why it cannot be opened."""
    app_state = websocket.app.state
    db_mgr = getattr(app_state, "db_manager", None)
    if not (await effective_settings(db_mgr, caller.org_id, "sandboxes")).get("allow_terminal"):
        await _refuse(websocket, "disabled", "Terminals are disabled by your organization.")
        return None
    if _open_terminals[session_id] >= MAX_TERMINALS_PER_SESSION:
        await _refuse(websocket, "too_many", "This session already has the maximum open terminals.")
        return None

    driver = getattr(app_state, "sandbox_driver", None)
    if driver is None or await driver.get_status(session_id) != SandboxStatus.RUNNING:
        await _refuse(
            websocket,
            "sandbox_not_running",
            "The sandbox is not running. Send a message to start it, then open the terminal.",
        )
        return None
    try:
        pty: PtyChannel = await driver.open_pty(session_id, cols, rows)
    except NotImplementedError:
        await _refuse(websocket, "unsupported", "This sandbox driver has no interactive terminal.")
        return None
    except Exception as err:
        logger.warning("Could not open a terminal for %s: %s", session_id, err)
        await _refuse(websocket, "unavailable", "Could not open a terminal in the sandbox.")
        return None
    return pty


class _TerminalBridge:
    """Pumps bytes between the browser and the shell until either side or the idle timer ends."""

    def __init__(self, websocket: WebSocket, pty: PtyChannel) -> None:
        self._websocket = websocket
        self._pty = pty
        self._last_input = time.monotonic()

    async def run(self) -> str:
        """Block until the session ends; returns why: exit, closed, idle or error."""
        tasks = {
            asyncio.create_task(self._to_client()): "exit",
            asyncio.create_task(self._to_shell()): "closed",
            asyncio.create_task(self._idle_watchdog()): "idle",
        }
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if any(finished.exception() is not None for finished in done):
                return "error"
            return tasks[next(iter(done))]
        finally:
            # Shielded: when the server cancels this handler, anyio re-delivers the
            # cancellation on every await, which would abandon the pump tasks half-stopped.
            with anyio.CancelScope(shield=True):
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _to_client(self) -> None:
        while True:
            data = await self._pty.read()
            if not data:
                return
            await self._websocket.send_bytes(data)

    async def _to_shell(self) -> None:
        while True:
            message = await self._websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            self._last_input = time.monotonic()
            if message.get("bytes"):
                if len(message["bytes"]) > MAX_INPUT_CHUNK_BYTES:
                    raise ValueError("input frame too large")
                await self._pty.write(message["bytes"])
            elif message.get("text"):
                await self._handle_control(message["text"])

    async def _handle_control(self, text: str) -> None:
        with contextlib.suppress(ValueError):
            control = json.loads(text)
            if control.get("type") == "resize":
                await self._pty.resize(
                    max(10, min(500, int(control["cols"]))),
                    max(2, min(200, int(control["rows"]))),
                )
            elif control.get("type") == "ping":
                await self._websocket.send_json({"type": "pong"})

    async def _idle_watchdog(self) -> None:
        limit = _idle_limit_seconds()
        while True:
            remaining = self._last_input + limit - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(remaining)


async def _audit_terminal(
    audit: AuditService, caller: RequestContext, session_id: str, summary: str
) -> None:
    await audit.record(
        "tool_call",
        org_id=caller.org_id,
        actor_user_id=caller.user_id,
        session_id=session_id,
        tool_name="terminal",
        result_summary=summary,
    )


@router.websocket("/sessions/{session_id}/terminal")
async def session_terminal(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(default=""),
    cols: int = Query(default=80, ge=10, le=500),
    rows: int = Query(default=24, ge=2, le=200),
) -> None:
    caller = await _authorise(websocket, session_id, token)
    if caller is None:
        return
    pty = await _open_shell(websocket, caller, session_id, cols, rows)
    if pty is None:
        return

    await websocket.accept()
    _open_terminals[session_id] += 1
    audit = AuditService(getattr(websocket.app.state, "db_manager", None))
    opened_at = time.monotonic()
    await _audit_terminal(audit, caller, session_id, "opened an interactive terminal")
    reason = "closed"
    try:
        reason = await _TerminalBridge(websocket, pty).run()
    finally:
        # The server may be cancelling this handler (shutdown, client gone); the shell must
        # still be closed and the close audited, so cleanup is shielded from that cancellation.
        with anyio.CancelScope(shield=True):
            await pty.close()
            _open_terminals[session_id] -= 1
            if _open_terminals[session_id] <= 0:
                _open_terminals.pop(session_id, None)
            with contextlib.suppress(Exception):
                await websocket.send_json({"type": "exit", "reason": reason})
                await websocket.close()
            elapsed = int(time.monotonic() - opened_at)
            await _audit_terminal(
                audit, caller, session_id, f"closed the terminal ({reason}) after {elapsed}s"
            )
