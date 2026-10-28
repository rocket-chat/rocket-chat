"""One live connection to a member's session list: where each conversation they can see stands."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import HTTPException, WebSocket, WebSocketDisconnect

from api.auth.access import _check_session_access
from api.auth.identity import (
    attach_api_token_identity,
    resolve_identity,
    resolve_token_identity,
)
from api.auth.roles import with_live_roles
from api.db.session import current_tenant_org_id
from api.services.metrics import websocket_connection_closed, websocket_connection_opened
from specifications.interfaces.events import (
    EventBusProtocol,
    EventType,
    SessionEvent,
    organization_channel,
)
from specifications.interfaces.session import SessionStoreProtocol

logger = logging.getLogger(__name__)


class SidebarSocket:
    """Relays the ``session_updated`` events of the member's organization they may see.

    The organization's topic carries every session's updates; whether this member may see one is
    decided per event against the session as stored now, by the same rule as opening it, so a
    conversation unshared a moment ago is not reported. The client lists its sessions again when
    the socket opens (``ready``), which covers whatever happened while it was not connected.
    """

    def __init__(self, websocket: WebSocket, token: str = "") -> None:
        self._ws = websocket
        self._token = token
        state = websocket.app.state
        self._store: SessionStoreProtocol = state.session_store
        self._event_bus: EventBusProtocol = state.event_bus
        self._db = getattr(state, "db_manager", None)
        self._user_id = ""
        self._org_id = ""
        self._roles: list[str] = []

    async def serve(self) -> None:
        if not await self._authorise():
            return
        await self._ws.accept()
        websocket_connection_opened()
        try:
            if getattr(self._ws.app.state, "draining", False):
                await self._ws.send_json({"type": "draining"})
                await self._ws.close(code=1001, reason="server draining")
                return
            channel = organization_channel(self._org_id)
            await self._event_bus.attach(channel)
            queue, close_queue = self._event_bus.open_queue(channel)
            relay = asyncio.create_task(self._relay(queue))
            try:
                await self._ws.send_json({"type": "ready"})
                await self._receive_frames()
            except WebSocketDisconnect:
                logger.debug("Sidebar socket closed for %s", self._user_id)
            finally:
                close_queue()
                relay.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await relay
        finally:
            websocket_connection_closed()

    async def _authorise(self) -> bool:
        # The same resolver as the session socket: cookie, bearer header or the `token` query
        # parameter browsers use because WebSockets cannot set headers.
        await attach_api_token_identity(self._ws, self._db, self._token)
        caller = resolve_identity(self._ws) or resolve_token_identity(self._token)
        if caller is not None:
            caller = await with_live_roles(caller, self._db)
        if caller is None:
            await self._ws.close(code=1008, reason="Unauthorized: authentication required")
            return False
        # The tenant middleware does not run for WebSockets: without this, row-level security
        # hides every session from the access check.
        current_tenant_org_id.set(caller.org_id)
        self._user_id = caller.user_id
        self._org_id = caller.org_id
        self._roles = list(caller.roles)
        return True

    async def _relay(self, queue: asyncio.Queue[SessionEvent]) -> None:
        while True:
            event = await queue.get()
            if event.event_type is not EventType.SESSION_UPDATED:
                continue
            try:
                if await self._may_see(str(event.payload.get("session_id") or "")):
                    await self._ws.send_json(_update_frame(event))
            except WebSocketDisconnect:
                return
            except Exception:
                logger.exception("Could not relay a session update to a sidebar")

    async def _may_see(self, session_id: str) -> bool:
        if not session_id:
            return False
        record = await self._store.get_session(session_id, self._org_id, include_history=False)
        if record is None or record.tenant_org_id != self._org_id:
            return False
        try:
            _check_session_access(record, self._user_id, self._roles)
        except HTTPException:
            return False
        return True

    async def _receive_frames(self) -> None:
        while True:
            try:
                data = json.loads(await self._ws.receive_text())
            except WebSocketDisconnect:
                raise
            except Exception:
                continue
            if isinstance(data, dict) and data.get("type") == "ping":
                await self._ws.send_json({"type": "pong"})


def _update_frame(event: SessionEvent) -> dict[str, Any]:
    return {
        "event_type": event.event_type.value,
        "session_id": event.payload.get("session_id"),
        "payload": event.payload,
        "timestamp_ms": event.timestamp_ms,
    }
