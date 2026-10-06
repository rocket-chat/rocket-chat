"""Tests for the agent task checklist tool and per-user session access control."""

from typing import Any

import pytest
from agent_core.tools.registry import ToolRegistry
from api.main import app
from fastapi.testclient import TestClient


@pytest.mark.asyncio
async def test_update_task_checklist_invokes_plan_callback() -> None:
    registry = ToolRegistry()
    received: list[list[dict[str, Any]]] = []
    registry.set_plan_callback(received.append)

    tasks = [
        {"id": "1", "title": "Read code", "status": "completed"},
        {"id": "2", "title": "Write tests", "status": "in_progress"},
    ]
    result = await registry.call_tool("update_task_checklist", {"tasks": tasks})

    assert received == [tasks]
    assert "1/2" in result
    assert "update_task_checklist" in {t.name for t in registry.get_tool_definitions()}


def _headers(user: str, role: str = "developer") -> dict[str, str]:
    return {"X-Tenant-User-Id": user, "X-User-Role": role}


def test_sessions_are_private_by_default_and_shareable() -> None:
    with TestClient(app) as client:
        created = client.post("/v1/sessions", json={"title": "private"}, headers=_headers("alice"))
        assert created.status_code == 200
        session_id = created.json()["session_id"]

        assert client.get(f"/v1/sessions/{session_id}", headers=_headers("bob")).status_code == 403
        listed = client.get("/v1/sessions", headers=_headers("bob")).json()
        assert session_id not in {s["session_id"] for s in listed}

        denied_share = client.post(
            f"/v1/sessions/{session_id}/share",
            json={"is_shared": True},
            headers=_headers("bob"),
        )
        assert denied_share.status_code == 403

        shared = client.post(
            f"/v1/sessions/{session_id}/share",
            json={"is_shared": True},
            headers=_headers("alice"),
        )
        assert shared.status_code == 200
        assert client.get(f"/v1/sessions/{session_id}", headers=_headers("bob")).status_code == 200

        assert (
            client.delete(f"/v1/sessions/{session_id}", headers=_headers("bob")).status_code == 403
        )
        assert (
            client.delete(f"/v1/sessions/{session_id}", headers=_headers("alice")).status_code
            == 200
        )


def test_readonly_role_cannot_write() -> None:
    with TestClient(app) as client:
        created = client.post("/v1/sessions", json={"title": "ro"}, headers=_headers("carol"))
        session_id = created.json()["session_id"]
        client.post(
            f"/v1/sessions/{session_id}/share",
            json={"is_shared": True},
            headers=_headers("carol"),
        )
        reader = _headers("dave", "readonly")
        assert client.get(f"/v1/sessions/{session_id}", headers=reader).status_code == 200
        patch = client.patch(f"/v1/sessions/{session_id}", json={"title": "x"}, headers=reader)
        assert patch.status_code == 403
        assert client.post("/v1/sessions", json={}, headers=reader).status_code == 403
