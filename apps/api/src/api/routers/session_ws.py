"""The session WebSocket: live events and steering frames."""

from fastapi import APIRouter, Query, WebSocket

from api.sockets.session_socket import WRITE_FRAMES, SessionSocket

router = APIRouter(prefix="/v1", tags=["Sessions & Telemetry"])

# Re-exported for tests that assert which frames the read-only role may not send.
_WS_WRITE_FRAMES = WRITE_FRAMES


@router.websocket("/sessions/{session_id}/ws")
async def session_websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(default=""),
) -> None:
    """Stream SessionEvents and tool states to the client; accept steering frames from it."""
    await SessionSocket(websocket, session_id, token).serve()
