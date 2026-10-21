"""Provider-key and budget endpoints: no secret ever leaves, roles and policy are enforced."""

from __future__ import annotations

import pytest
from api.main import app
from api.services.settings import save_org_config
from fastapi.testclient import TestClient

ADMIN = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "admin_user", "X-User-Role": "admin"}
MEMBER = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "member_user",
    "X-User-Role": "developer",
}
READONLY = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "user_1", "X-User-Role": "readonly"}
OTHER_ORG = {
    "X-Tenant-Org-Id": "usage_org",
    "X-Tenant-User-Id": "usage_user",
    "X-User-Role": "admin",
}
SECRET = "sk-ant-api03-DO-NOT-LEAK-ME-9999"


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        app.state.db_manager = None  # the process-local development stores
        from api.services.model_credentials import ModelCredentialStore

        app.state.model_credentials = ModelCredentialStore(None)
        yield c


def test_a_member_stores_a_key_and_only_a_fingerprint_ever_comes_back(client: TestClient) -> None:
    put = client.put("/v1/user/model-keys/anthropic", json={"api_key": SECRET}, headers=MEMBER)
    assert put.status_code == 200 and SECRET not in put.text
    assert put.json()["fingerprint"] == "sk-...9999"

    listed = client.get("/v1/user/model-keys", headers=MEMBER)
    assert [k["provider"] for k in listed.json()] == ["anthropic"]
    assert SECRET not in listed.text and "DO-NOT-LEAK" not in listed.text


def test_keys_are_private_to_the_member_and_the_organization(client: TestClient) -> None:
    client.put("/v1/user/model-keys/anthropic", json={"api_key": SECRET}, headers=MEMBER)

    assert (
        client.get("/v1/user/model-keys", headers={**MEMBER, "X-Tenant-User-Id": "user_2"}).json()
        == []
    )
    assert client.get("/v1/user/model-keys", headers=OTHER_ORG).json() == []
    assert client.get("/v1/admin/model-keys", headers=ADMIN).json() == [], (
        "personal keys are not org keys"
    )


def test_only_administrators_manage_the_organizations_keys(client: TestClient) -> None:
    assert (
        client.put(
            "/v1/admin/model-keys/openai", json={"api_key": SECRET}, headers=MEMBER
        ).status_code
        == 403
    )
    assert client.get("/v1/admin/model-keys", headers=MEMBER).status_code == 403
    assert (
        client.put(
            "/v1/admin/model-keys/openai", json={"api_key": SECRET}, headers=ADMIN
        ).status_code
        == 200
    )
    assert [k["provider"] for k in client.get("/v1/admin/model-keys", headers=ADMIN).json()] == [
        "openai"
    ]
    assert client.delete("/v1/admin/model-keys/openai", headers=MEMBER).status_code == 403
    assert client.delete("/v1/admin/model-keys/openai", headers=ADMIN).json() == {"deleted": True}


def test_read_only_members_cannot_store_keys(client: TestClient) -> None:
    res = client.put("/v1/user/model-keys/anthropic", json={"api_key": SECRET}, headers=READONLY)
    assert res.status_code == 403


@pytest.mark.setting("budgets.byok_policy")
@pytest.mark.asyncio
async def test_the_org_policy_can_forbid_bring_your_own_key(client: TestClient) -> None:
    await save_org_config(None, "test_org", "budgets", {"byok_policy": "platform_only"}, [])
    assert (
        client.put(
            "/v1/user/model-keys/anthropic", json={"api_key": SECRET}, headers=MEMBER
        ).status_code
        == 403
    )
    assert (
        client.put(
            "/v1/admin/model-keys/anthropic", json={"api_key": SECRET}, headers=ADMIN
        ).status_code
        == 403
    )

    await save_org_config(None, "test_org", "budgets", {"byok_policy": "org_key"}, [])
    assert (
        client.put(
            "/v1/user/model-keys/anthropic", json={"api_key": SECRET}, headers=MEMBER
        ).status_code
        == 403
    )
    assert (
        client.put(
            "/v1/admin/model-keys/anthropic", json={"api_key": SECRET}, headers=ADMIN
        ).status_code
        == 200
    )


def test_a_members_key_cannot_point_at_an_internal_endpoint(client: TestClient) -> None:
    internal = client.put(
        "/v1/user/model-keys/openai",
        json={"api_key": SECRET, "api_base": "http://169.254.169.254/v1"},
        headers=MEMBER,
    )
    assert internal.status_code == 422 and "api_base" in internal.json()["detail"]

    # An administrator may name their private vLLM/Ollama - that is the point of a base URL.
    private = client.put(
        "/v1/admin/model-keys/openai",
        json={"api_key": SECRET, "api_base": "http://vllm.internal:8000/v1"},
        headers=ADMIN,
    )
    assert (
        private.status_code == 200 and private.json()["api_base"] == "http://vllm.internal:8000/v1"
    )


def test_budget_cap_validation_and_admin_only(client: TestClient) -> None:
    assert (
        client.put(
            "/v1/admin/budget", json={"monthly_budget_cap_usd": 50}, headers=MEMBER
        ).status_code
        == 403
    )
    for bad in (-1, "abc", None, "nan", "inf"):
        res = client.put("/v1/admin/budget", json={"monthly_budget_cap_usd": bad}, headers=ADMIN)
        assert res.status_code == 422, bad
    ok = client.put("/v1/admin/budget", json={"monthly_budget_cap_usd": 50}, headers=ADMIN)
    assert ok.status_code == 200 and ok.json()["monthly_budget_cap_usd"] == 50.0


def test_the_cap_is_persisted_as_a_setting_and_reported_by_the_usage_endpoint(
    client: TestClient,
) -> None:
    client.put("/v1/admin/budget", json={"monthly_budget_cap_usd": 120}, headers=ADMIN)

    settings = client.get("/v1/settings/budgets", headers=ADMIN).json()
    usage = client.get("/v1/settings/org/usage", headers=ADMIN).json()

    assert settings["effective"]["monthly_budget_usd"] == 120.0
    assert usage["monthly_budget_cap_usd"] == 120.0 and usage["tenant_org_id"] == "test_org"
    assert "period_start" in usage and "models" in usage


def test_members_cannot_change_the_organizations_budget_settings(client: TestClient) -> None:
    res = client.put(
        "/v1/settings/budgets", json={"overrides": {"monthly_budget_usd": 1000000}}, headers=MEMBER
    )
    assert res.status_code == 422


def test_administrators_see_which_providers_the_deployment_holds_a_key_for(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in (
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "GEMINI_API_KEY",
        "AZURE_API_KEY",
        "MISTRAL_API_KEY",
        "GROQ_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    monkeypatch.setenv("OPENAI_API_KEY", "   ")

    listed = client.get("/v1/admin/model-keys/platform", headers=ADMIN)

    assert listed.status_code == 200
    assert listed.json() == [
        {"provider": "openrouter", "env_var": "OPENROUTER_API_KEY", "fingerprint": "sk-...9999"}
    ]
    assert SECRET not in listed.text and "DO-NOT-LEAK" not in listed.text


def test_the_deployments_keys_are_for_administrators_only(client: TestClient) -> None:
    assert client.get("/v1/admin/model-keys/platform", headers=MEMBER).status_code == 403


def test_an_organization_key_sits_beside_the_deployments_without_replacing_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    client.put("/v1/admin/model-keys/anthropic", json={"api_key": SECRET + "-ORG"}, headers=ADMIN)

    organization = client.get("/v1/admin/model-keys", headers=ADMIN).json()
    deployment = client.get("/v1/admin/model-keys/platform", headers=ADMIN).json()

    assert [k["provider"] for k in organization] == ["anthropic"]
    assert "anthropic" in [k["provider"] for k in deployment]
