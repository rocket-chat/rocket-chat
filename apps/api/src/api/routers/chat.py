"""Microsoft Teams' messaging endpoint (the Azure Bot's "Messaging endpoint")."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from chat_bridges.teams import TeamsAuthError, TeamsPlatform
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/v1/chat", tags=["Chat platforms"])
logger = logging.getLogger(__name__)
_background: set[asyncio.Task[None]] = set()


@router.post("/teams/messages", status_code=202)
async def teams_messages(request: Request) -> dict[str, Any]:
    """A Bot Framework activity: its token is checked, then it is answered in the background
    (Teams expects a reply within seconds; the answer arrives as messages in the thread)."""
    bridges = getattr(request.app.state, "chat_bridges", None)
    bridge = getattr(bridges, "teams", None)
    if bridge is None or not isinstance(bridge.platform, TeamsPlatform):
        raise HTTPException(status_code=404, detail="Microsoft Teams is not configured")
    try:
        await bridge.platform.verify(request.headers.get("authorization"))
    except TeamsAuthError as err:
        raise HTTPException(status_code=401, detail=str(err)) from err
    message = bridge.platform.message_of(await request.json())
    if message is None:
        return {"status": "ignored"}
    task = asyncio.create_task(bridge.handle(message))
    _background.add(task)
    task.add_done_callback(_background.discard)
    return {"status": "accepted"}
