"""Slack connection status and test."""

import logging
from typing import Any

from fastapi import (
    APIRouter,
    Request,
)

from api.auth.access import _get_request_identity

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Sessions & Telemetry"])


def _org_assistant(request: Request) -> Any:
    """The first running bot of the caller's organization (never another organization's)."""
    org_id, _, _ = _get_request_identity(request)
    manager = getattr(request.app.state, "slack_bot_manager", None)
    bots = manager.for_org(org_id) if manager is not None else []
    return bots[0] if bots else None


@router.get("/slack/status")
async def get_slack_status(request: Request) -> dict[str, Any]:
    """Return real connection state of embedded Slack Socket Mode Assistant."""
    assistant = _org_assistant(request)
    if not assistant:
        return {
            "is_configured": False,
            "is_connected": False,
            "connected": False,
            "socket_mode_active": False,
            "bot_user_id": None,
            "channels_linked_count": 0,
            "channels_linked": 0,
            "channels": [],
            "quota_counters": {},
            "status_message": "No Slack bot is configured for this organization",
        }

    has_tokens = bool(assistant.settings.slack_bot_token and assistant.settings.slack_app_token)
    is_active = False
    if hasattr(assistant, "is_connected"):
        try:
            is_active = await assistant.is_connected()
        except Exception:
            is_active = False
    elif assistant.handler is not None:
        is_active = True

    channels_count = len(assistant._last_activity)

    # Unauthenticated-path observability: rate-limit and quota counters are
    # exposed here so operators can see rejections without scraping logs. The assistant
    # owns the counters; a missing method (older assistant) degrades to an empty map.
    quota_counters: dict[str, int] = {}
    if hasattr(assistant, "quota_counters"):
        try:
            quota_counters = assistant.quota_counters()
        except Exception as err:
            logger.debug("Unable to read Slack quota counters: %s", err)

    return {
        "is_configured": has_tokens,
        "is_connected": is_active,
        "connected": is_active,
        "socket_mode_active": is_active,
        "bot_user_id": getattr(assistant, "bot_user_id", None) or "U_ROCKET_ASSISTANT",
        "bot_user_name": getattr(assistant, "bot_user_name", None),
        "team_name": getattr(assistant, "team_name", None),
        "team_id": getattr(assistant, "team_id", None),
        "channels_linked_count": channels_count,
        "channels_linked": channels_count,
        "channels": list(assistant._last_activity.keys()),
        "quota_counters": quota_counters,
        "status_message": (
            "Socket Mode Active"
            if is_active
            else ("Connecting..." if assistant.handler is not None else "Disconnected")
        ),
    }


@router.post("/slack/test")
async def test_slack_socket_connection(request: Request) -> dict[str, Any]:
    """Test real Slack API authentication and Socket Mode readiness."""
    assistant = _org_assistant(request)
    if not assistant:
        return {
            "ok": False,
            "success": False,
            "message": "No Slack bot is running for this organization. Add one in Settings > Slack.",
        }

    if not assistant.settings.slack_bot_token or not assistant.settings.slack_app_token:
        return {
            "ok": False,
            "success": False,
            "message": "The Slack bot has no tokens.",
        }

    try:
        auth_test_res = await assistant.app.client.auth_test()
        user_name = auth_test_res.get("user", "Rocket Assistant")
        team_name = auth_test_res.get("team", "Workspace")
        bot_user_id = auth_test_res.get("user_id")
        return {
            "ok": True,
            "success": True,
            "message": f"Connected successfully to Slack team '{team_name}' as user '{user_name}'.",
            "team": team_name,
            "user": user_name,
            "bot_user_id": bot_user_id,
        }
    except Exception as err:
        return {
            "ok": False,
            "success": False,
            "message": f"Slack auth_test error: {err}",
        }
