"""Credentials with an identity, and the rule that picks one for a call (P3.1, P3.2)."""

from __future__ import annotations

import pytest
from api.services.model_credentials import (
    CredentialChanges,
    CredentialResolver,
    ModelCredentialStore,
    NewCredential,
)
from api.services.settings import reset_memory_settings, save_org_config

from specifications.interfaces.llm import CredentialBinding, CredentialRefusedError, ModelScope

ORG = "creds_org"


@pytest.fixture(autouse=True)
def _clean() -> None:
    reset_memory_settings()


async def _store() -> tuple[ModelCredentialStore, dict[str, str]]:
    store = ModelCredentialStore(None)
    ids = {
        "org": (
            await store.create(
                ORG, NewCredential("org", None, "anthropic", "sk-org-default-1", "Default")
            )
        ).id,
        "pro": (
            await store.create(
                ORG,
                NewCredential(
                    "org",
                    None,
                    "anthropic",
                    "sk-org-pro-0001",
                    "Claude Pro",
                    restricted_to_agents=True,
                    default_for_provider=False,
                ),
            )
        ).id,
        "alice": (
            await store.create(
                ORG, NewCredential("member", "alice", "anthropic", "sk-alice-0001", "Alice")
            )
        ).id,
    }
    return store, ids


def _resolver(store: ModelCredentialStore, allowed: bool = True) -> CredentialResolver:
    async def may_use(org_id: str, user_id: str) -> bool:
        return allowed

    return CredentialResolver(None, store, member_may_use_keys=may_use)


async def test_a_bound_restricted_key_serves_its_agent_only() -> None:
    store, ids = await _store()
    resolver = _resolver(store, allowed=False)
    bound = ModelScope(
        org_id=ORG,
        user_id="bob",
        agent_id="agent-pro",
        credential_bindings=(CredentialBinding("anthropic", ids["pro"]),),
    )
    resolved = await resolver.resolve(bound, "anthropic/claude-sonnet-5-5")
    assert resolved is not None
    assert (resolved.key_source, resolved.credential_id) == ("agent", ids["pro"])
    assert resolved.credentials.api_key == "sk-org-pro-0001"
    other = await resolver.resolve(
        ModelScope(org_id=ORG, user_id="bob"), "anthropic/claude-sonnet-5-5"
    )
    assert other is not None and other.credential_id == ids["org"]


async def test_a_strict_binding_refuses_another_provider() -> None:
    store, ids = await _store()
    scope = ModelScope(
        org_id=ORG,
        user_id="bob",
        agent_id="agent-pro",
        credential_bindings=(CredentialBinding("anthropic", ids["pro"]),),
        strict_binding=True,
    )
    with pytest.raises(CredentialRefusedError) as refused:
        await _resolver(store).resolve(scope, "openai/gpt-5")
    assert refused.value.reason == "unbound"


async def test_a_deleted_binding_is_withdrawn_not_billed_elsewhere() -> None:
    store, ids = await _store()
    await store.delete(ORG, ids["pro"])
    scope = ModelScope(
        org_id=ORG,
        user_id="bob",
        agent_id="agent-pro",
        credential_bindings=(CredentialBinding("anthropic", ids["pro"]),),
    )
    with pytest.raises(CredentialRefusedError) as refused:
        await _resolver(store).resolve(scope, "anthropic/claude-sonnet-5-5")
    assert refused.value.reason == "withdrawn"


@pytest.mark.setting("models.member_keys")
async def test_member_keys_order_and_off() -> None:
    store, ids = await _store()
    alice = ModelScope(org_id=ORG, user_id="owner", requester_user_id="alice")
    resolver = _resolver(store)
    preferred = await resolver.resolve(alice, "anthropic/claude-sonnet-5-5")
    assert preferred is not None and preferred.credential_id == ids["alice"]
    await save_org_config(None, ORG, "models", {"member_keys": "fallback"}, [])
    fallback = await resolver.resolve(alice, "anthropic/claude-sonnet-5-5")
    assert fallback is not None and fallback.credential_id == ids["org"]
    await save_org_config(None, ORG, "models", {"member_keys": "off"}, [])
    await store.delete(ORG, ids["org"])
    assert await resolver.resolve(alice, "anthropic/claude-sonnet-5-5") is None


async def test_a_member_key_needs_the_capability_now() -> None:
    store, ids = await _store()
    alice = ModelScope(org_id=ORG, user_id="alice")
    resolved = await _resolver(store, allowed=False).resolve(alice, "anthropic/claude-sonnet-5-5")
    assert resolved is not None and resolved.credential_id == ids["org"]


async def test_a_shared_member_key_serves_the_org_agent_until_unshared() -> None:
    store, ids = await _store()
    await store.update(ORG, ids["alice"], CredentialChanges(shared_with_org_agents=("agent-pro",)))
    scope = ModelScope(
        org_id=ORG,
        user_id="bob",
        agent_id="agent-pro",
        credential_bindings=(CredentialBinding("anthropic", ids["alice"]),),
    )
    resolver = _resolver(store)
    resolved = await resolver.resolve(scope, "anthropic/claude-sonnet-5-5")
    assert resolved is not None and resolved.key_source == "agent"
    await store.update(ORG, ids["alice"], CredentialChanges(shared_with_org_agents=()))
    with pytest.raises(CredentialRefusedError):
        await resolver.resolve(scope, "anthropic/claude-sonnet-5-5")


async def test_one_default_per_owner_and_provider_and_rotation_keeps_the_id() -> None:
    store, ids = await _store()
    second = await store.create(
        ORG, NewCredential("org", None, "anthropic", "sk-org-second-1", "Second")
    )
    defaults = [c for c in await store.list(ORG, owner_kind="org") if c.default_for_provider]
    assert [c.id for c in defaults] == [second.id]
    rotated = await store.rotate(ORG, ids["org"], "sk-org-rotated-1")
    assert rotated is not None and rotated.id == ids["org"]
    secret = await store.secret(ORG, ids["org"])
    assert secret is not None and secret.api_key == "sk-org-rotated-1"
