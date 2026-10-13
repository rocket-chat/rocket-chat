"""Built-in agent edits on PostgreSQL: one row per organization, isolated, resettable."""

from __future__ import annotations

import os

import pytest
from api.db.models import AgentPersonaEntityModel
from api.db.store import ensure_tenant_identity
from api.main import app
from fastapi.testclient import TestClient
from sqlalchemy import select

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="needs PostgreSQL")

ADMIN = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "persona_admin", "X-User-Role": "admin"}
OTHER = {
    "X-Tenant-Org-Id": "usage_org",
    "X-Tenant-User-Id": "persona_other",
    "X-User-Role": "admin",
}
BODY = {
    "id": "persona-general",
    "name": "Edited engineer",
    "role_title": "Engineer",
    "system_prompt": "Edited for the organization.",
}


def _general(client: TestClient, headers: dict[str, str]) -> dict[str, object]:
    return next(
        a for a in client.get("/v1/agents", headers=headers).json() if a["id"] == "persona-general"
    )


def test_an_edit_is_stored_once_per_organization_and_reset_removes_it() -> None:
    with TestClient(app) as client:
        manager = app.state.db_manager

        async def provision() -> None:
            for org, user in (("test_org", "persona_admin"), ("usage_org", "persona_other")):
                async with manager.session(tenant_org_id=org) as session:
                    await ensure_tenant_identity(session, org, user)

        async def rows(org: str) -> int:
            async with manager.session(tenant_org_id=org) as session:
                found = await session.execute(
                    select(AgentPersonaEntityModel).where(
                        AgentPersonaEntityModel.overrides_builtin == "persona-general"
                    )
                )
                return len(found.scalars().all())

        client.portal.call(provision)
        client.delete("/v1/agents/persona-general", headers=ADMIN)

        for _ in range(2):
            assert client.post("/v1/agents", json=BODY, headers=ADMIN).status_code == 200
        assert _general(client, ADMIN)["name"] == "Edited engineer"
        assert _general(client, ADMIN)["customized"] is True
        assert _general(client, OTHER)["name"] == "General Software Engineer"
        assert client.portal.call(rows, "test_org") == 1, "the second save updates the row"
        assert client.portal.call(rows, "usage_org") == 0, "row-level security isolates tenants"

        client.delete("/v1/agents/persona-general", headers=ADMIN)
        assert _general(client, ADMIN)["customized"] is False
        assert client.portal.call(rows, "test_org") == 0
