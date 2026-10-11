"""Sharing with named members on PostgreSQL: emails resolve to this organization's members only."""

from __future__ import annotations

import os
import uuid

import pytest
from api.db.models import UserModel
from api.db.session import current_tenant_org_id
from api.db.store import ensure_tenant_identity
from api.main import app
from fastapi.testclient import TestClient
from sqlalchemy import update

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="needs PostgreSQL")

OWNER = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "share_owner",
    "X-User-Role": "developer",
}
GUEST = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "share_guest",
    "X-User-Role": "developer",
}


def test_emails_resolve_to_members_and_the_rest_are_reported() -> None:
    address = f"guest-{uuid.uuid4().hex[:6]}@acme.test"
    with TestClient(app) as client:
        manager = app.state.db_manager

        async def provision() -> None:
            current_tenant_org_id.set("test_org")
            async with manager.session(tenant_org_id="test_org") as session:
                await ensure_tenant_identity(session, "test_org", "share_guest")
                await ensure_tenant_identity(session, "test_org", "share_owner")
                await session.execute(
                    update(UserModel).where(UserModel.id == "share_guest").values(email=address)
                )

        client.portal.call(provision)
        session_id = client.post("/v1/sessions", json={"title": "pair"}, headers=OWNER).json()[
            "session_id"
        ]
        before = client.get(f"/v1/sessions/{session_id}", headers=GUEST).status_code

        shared = client.put(
            f"/v1/sessions/{session_id}/sharing",
            json={"org_wide": False, "emails": [address.upper(), "stranger@elsewhere.test"]},
            headers=OWNER,
        ).json()
        after = client.get(f"/v1/sessions/{session_id}", headers=GUEST).status_code
        client.delete(f"/v1/sessions/{session_id}", headers=OWNER)

    assert before == 403 and after == 200
    assert [m["email"] for m in shared["members"]] == [address]
    assert shared["unknown_emails"] == ["stranger@elsewhere.test"]
