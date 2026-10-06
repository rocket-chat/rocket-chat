"""Integration test verifying full cold process restart and state preservation.

Simulates cold restarts of the application control plane, verifying:
1. Sessions and multi-turn conversation history survive across independent server lifespans.
2. Active checklist and task states survive restart.
3. Organization admin lockouts and user-specific cascading settings survive restart.
4. Subsequent turns on resurrected sessions maintain continuous context and access controls.
"""

import os
import uuid

import pytest
from api.db.models import OrganizationModel, UserModel
from api.db.session import DatabaseSessionManager
from api.main import app
from fastapi.testclient import TestClient

DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://rocket_app:rocket_app@localhost:5432/rocket_chat",
)
ADMIN_DATABASE_URL = os.getenv(
    "ADMIN_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/rocket_chat",
)


@pytest.fixture(scope="module", autouse=True)
async def setup_database() -> None:
    """Ensure database schema and required tenant fixtures exist before testing."""
    admin_mgr = DatabaseSessionManager(ADMIN_DATABASE_URL)
    try:
        await admin_mgr.create_tables()
        async with admin_mgr.session() as s:
            org = await s.get(OrganizationModel, "restart_test_org")
            if not org:
                org = OrganizationModel(
                    id="restart_test_org",
                    name="Restart Verification Org",
                    slug=f"restart-org-{uuid.uuid4().hex[:6]}",
                )
                s.add(org)
                await s.flush()
            user = await s.get(UserModel, "restart_test_user")
            if not user:
                user = UserModel(
                    id="restart_test_user",
                    org_id="restart_test_org",
                    email="restart@rocket.chat",
                    name="Restart Tester",
                )
                s.add(user)
                await s.flush()
    finally:
        await admin_mgr.close()


def test_cold_restart_preserves_sessions_and_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Simulates a full cold restart cycle of the backend control plane:
    Instance 1:
      - Creates a session with custom title and metadata
      - Patches session with conversation history and checklist items
      - Configures organization-level locked settings (admin)
      - Configures user-level unlocked setting overrides
    SHUTDOWN:
      - Client 1 exits lifespan (simulating SIGTERM / server reboot)
    Instance 2 (Post-Restart):
      - Starts up fresh TestClient(app) connecting to the database
      - Validates that the session exists with 100% of its history and checklist
      - Validates that organization settings and admin lockouts are preserved
      - Validates that user overrides remain active and intact
      - Appends a new turn to verify continuous post-restart operation
    """
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("DEV_AUTH_BYPASS", "true")

    org_id = "restart_test_org"
    user_id = "restart_test_user"
    user_headers = {
        "X-Tenant-Org-Id": org_id,
        "X-Tenant-User-Id": user_id,
        "X-User-Role": "developer",
    }
    admin_headers = {
        "X-Tenant-Org-Id": org_id,
        "X-Tenant-User-Id": user_id,
        "X-User-Role": "admin",
    }

    session_id: str
    unique_message = f"Pre-restart flight telemetry instruction {uuid.uuid4().hex[:8]}"

    # ==========================================
    # LIFESPAN 1: First Server Instance
    # ==========================================
    with TestClient(app) as client1:
        # 1. Create a session
        create_res = client1.post(
            "/v1/sessions",
            json={"title": "Mission Alpha State Preservation"},
            headers=user_headers,
        )
        assert create_res.status_code == 200
        session_data = create_res.json()
        session_id = session_data["session_id"]
        assert session_data["title"] == "Mission Alpha State Preservation"

        # 2. Add conversation history and task checklist to session
        patch_res = client1.patch(
            f"/v1/sessions/{session_id}",
            json={
                "conversation_history": [
                    {"role": "user", "content": unique_message},
                    {"role": "assistant", "content": "Acknowledged. Telemetry locked."},
                ],
                "metadata": {
                    "checklist": [
                        {"id": "task-1", "title": "Inspect orbital engine", "status": "completed"},
                        {
                            "id": "task-2",
                            "title": "Deploy satellite probe",
                            "status": "in_progress",
                        },
                    ]
                },
            },
            headers=user_headers,
        )
        assert patch_res.status_code == 200

        # 3. Configure org settings with admin lockout
        admin_res = client1.put(
            "/v1/admin/settings/github",
            json={
                "config": {"commit_authorship_policy": "bot_only", "bot_author_name": "Rocket Ops"},
                "locked_keys": ["commit_authorship_policy"],
            },
            headers=admin_headers,
        )
        assert admin_res.status_code == 200

        # 4. Configure user settings overrides
        user_res = client1.put(
            "/v1/settings/github",
            json={"overrides": {"default_branch": "release-v1"}},
            headers=user_headers,
        )
        assert user_res.status_code == 200
        user_data = user_res.json()
        assert user_data["effective"]["default_branch"] == "release-v1"
        assert user_data["effective"]["commit_authorship_policy"] == "bot_only"
        assert user_data["field_status"]["commit_authorship_policy"] == "locked"

    # ==========================================
    # COLD SHUTDOWN OCCURRED HERE
    # client1 exited its lifespan context. In-memory dictionaries and active memory wiped.
    # ==========================================

    # ==========================================
    # LIFESPAN 2: Rebooted Server Instance
    # ==========================================
    with TestClient(app) as client2:
        # 1. Verify session rehydration from cold state
        get_res = client2.get(f"/v1/sessions/{session_id}", headers=user_headers)
        assert get_res.status_code == 200
        rehydrated = get_res.json()
        assert rehydrated["session_id"] == session_id
        assert rehydrated["title"] == "Mission Alpha State Preservation"

        # Check conversation history was preserved verbatim
        history = rehydrated.get("conversation_history", [])
        assert len(history) == 2
        assert history[0]["content"] == unique_message
        assert history[1]["content"] == "Acknowledged. Telemetry locked."

        # Check task checklist was preserved in metadata
        checklist = rehydrated.get("metadata", {}).get("checklist", [])
        assert len(checklist) == 2
        assert checklist[0]["id"] == "task-1"
        assert checklist[0]["status"] == "completed"
        assert checklist[1]["id"] == "task-2"
        assert checklist[1]["status"] == "in_progress"

        # 2. Verify settings and admin lockouts survived restart
        settings_res = client2.get("/v1/settings/github", headers=user_headers)
        assert settings_res.status_code == 200
        restored_settings = settings_res.json()
        assert restored_settings["effective"]["commit_authorship_policy"] == "bot_only"
        assert restored_settings["field_status"]["commit_authorship_policy"] == "locked"
        assert restored_settings["effective"]["default_branch"] == "release-v1"
        assert restored_settings["field_status"]["default_branch"] == "overridden"

        # 3. Verify subsequent update works seamlessly post-restart
        post_restart_msg = "Subsequent instruction transmitted after cold server restart."
        update_res = client2.patch(
            f"/v1/sessions/{session_id}",
            json={
                "conversation_history": [
                    *rehydrated["conversation_history"],
                    {"role": "user", "content": post_restart_msg},
                ]
            },
            headers=user_headers,
        )
        assert update_res.status_code == 200
        updated = update_res.json()
        assert len(updated["conversation_history"]) == 3
        assert updated["conversation_history"][2]["content"] == post_restart_msg

        # 4. Clean up test session
        del_res = client2.delete(f"/v1/sessions/{session_id}", headers=user_headers)
        assert del_res.status_code == 200
