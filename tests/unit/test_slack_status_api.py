"""Unit tests for Slack connection status and socket test endpoints."""

from api.main import app
from fastapi.testclient import TestClient


def test_slack_status_endpoint() -> None:
    client = TestClient(app)
    res = client.get("/v1/slack/status")
    assert res.status_code == 200
    data = res.json()
    assert "is_configured" in data
    assert "is_connected" in data
    assert "connected" in data
    assert "socket_mode_active" in data
    assert "channels_linked_count" in data
    assert "channels_linked" in data


def test_slack_test_connection_without_tokens_returns_graceful_response() -> None:
    client = TestClient(app)
    res = client.post("/v1/slack/test")
    assert res.status_code == 200
    data = res.json()
    assert "success" in data
    assert "ok" in data
    assert data["success"] is False
    assert data["ok"] is False


def test_slack_status_exposes_quota_counters() -> None:
    """The status endpoint surfaces rate-limit/quota counters."""
    from unittest.mock import AsyncMock, MagicMock

    fake = MagicMock()
    fake.settings.slack_bot_token = "xoxb-test"
    fake.settings.slack_app_token = "xapp-test"
    fake.is_connected = AsyncMock(return_value=True)
    fake.quota_counters = MagicMock(return_value={"quota_denied": 3, "turns_admitted": 7})
    fake._last_activity = {}
    fake.handler = None
    fake.bot_user_id = None
    fake.bot_user_name = None
    fake.team_name = None
    fake.team_id = None

    original = getattr(app.state, "slack_assistant", None)
    app.state.slack_assistant = fake
    try:
        client = TestClient(app)
        data = client.get("/v1/slack/status").json()
    finally:
        app.state.slack_assistant = original

    assert data["quota_counters"] == {"quota_denied": 3, "turns_admitted": 7}
    assert data["is_configured"] is True
