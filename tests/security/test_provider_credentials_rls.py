"""Model provider credentials are each organization's own, under forced Row-Level Security."""

from __future__ import annotations

from api.db.models import ProviderCredentialModel
from api.db.session import DatabaseSessionManager
from api.services.model_credentials import ModelCredentialStore

from tests.support.rls import (
    TEST_DATABASE_URL,
    add_row,
    assert_cannot_write_into,
    assert_isolated,
    needs_postgres,
    new_id,
    two_orgs,
)


def _credential(org_id: str) -> ProviderCredentialModel:
    return ProviderCredentialModel(
        id=new_id("cred"),
        org_id=org_id,
        label="Claude",
        owner_kind="org",
        provider="anthropic",
        kind="api_key",
        encrypted_secret="sealed",
        key_fingerprint="sk-ant...0001",
    )


@needs_postgres
async def test_provider_credentials_are_isolated_in_postgres() -> None:
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    try:
        org_a, org_b = await two_orgs(db)
        credential = _credential(org_a)
        await add_row(db, org_a, credential)
        store = ModelCredentialStore(db)

        await assert_isolated(db, ProviderCredentialModel, org_a, org_b)
        assert await store.get(org_b, credential.id) is None
        assert await store.delete(org_b, credential.id) is None
        assert await store.get(org_a, credential.id) is not None, "a foreign delete removed nothing"
        await assert_cannot_write_into(db, org_b, _credential(org_a))
    finally:
        await db.close()
