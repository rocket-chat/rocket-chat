"""A member's signing key on PostgreSQL: encrypted at rest, private to its owner, removable."""

from __future__ import annotations

import os
import pathlib

import pytest
from api.db.models import UserCredentialModel
from api.db.session import current_tenant_org_id
from api.db.store import ensure_tenant_identity
from api.main import app
from api.services.git_signing import gpg_available
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.support.gpg_keys import make_key

pytestmark = [
    pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="needs PostgreSQL"),
    pytest.mark.skipif(not gpg_available(), reason="needs GnuPG"),
]

OWNER = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "sign_owner",
    "X-User-Role": "developer",
}
OTHER = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "sign_other",
    "X-User-Role": "developer",
}

# Long and distinctive: a short one turns up by chance in the base64 ciphertext.
SECRET_PASSPHRASE = "correct-horse-battery-staple"


def test_the_key_is_encrypted_at_rest_private_to_its_owner_and_removable(
    tmp_path: pathlib.Path,
) -> None:
    private, _ = make_key(tmp_path, "Owner <owner@example.com>", passphrase=SECRET_PASSPHRASE)
    with TestClient(app) as client:
        manager = app.state.db_manager

        async def provision() -> None:
            async with manager.session(tenant_org_id="test_org") as session:
                await ensure_tenant_identity(session, "test_org", "sign_owner")
                await ensure_tenant_identity(session, "test_org", "sign_other")

        async def stored() -> list[str]:
            current_tenant_org_id.set("test_org")
            async with manager.session(tenant_org_id="test_org") as session:
                rows = await session.execute(
                    select(UserCredentialModel.encrypted_secret).where(
                        UserCredentialModel.provider == "git_signing",
                        UserCredentialModel.user_id == "sign_owner",
                    )
                )
                return [str(r) for r in rows.scalars()]

        client.portal.call(provision)
        try:
            saved = client.put(
                "/v1/user/git-signing",
                json={"private_key": private, "passphrase": SECRET_PASSPHRASE},
                headers=OWNER,
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["commit_email"] == "owner@example.com"

            secrets = client.portal.call(stored)
            assert (
                len(secrets) == 1
                and "PRIVATE KEY" not in secrets[0]
                and SECRET_PASSPHRASE not in secrets[0]
            )

            assert client.get("/v1/user/git-signing", headers=OTHER).json()["configured"] is False
        finally:
            removed = client.delete("/v1/user/git-signing", headers=OWNER)
        assert removed.json()["configured"] is False
        assert client.portal.call(stored) == []
