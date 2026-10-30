"""Model credential endpoints: no secret ever leaves, roles, capabilities and endpoints are
enforced (``/v1/user/credentials``, ``/v1/admin/credentials``)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from api.main import app
from api.services.settings import reset_memory_settings, save_org_config
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
def client() -> Iterator[TestClient]:
    reset_memory_settings()
    with TestClient(app) as c:
        app.state.db_manager = None  # the process-local development stores
        from api.services.model_credentials import ModelCredentialStore

        app.state.model_credentials = ModelCredentialStore(None)
        yield c


def _key(provider: str = "anthropic", **extra: object) -> dict[str, object]:
    return {"provider": provider, "api_key": SECRET, "label": "k", **extra}


def test_a_member_stores_a_key_and_only_a_fingerprint_ever_comes_back(client: TestClient) -> None:
    created = client.post("/v1/user/credentials", json=_key(), headers=MEMBER)
    assert created.status_code == 200, created.text
    listed = client.get("/v1/user/credentials", headers=MEMBER)
    assert SECRET not in created.text and SECRET not in listed.text
    assert listed.json()[0]["fingerprint"].endswith("9999")


def test_keys_are_private_to_the_member_and_the_organization(client: TestClient) -> None:
    client.post("/v1/user/credentials", json=_key(), headers=MEMBER)
    client.post("/v1/admin/credentials", json=_key(), headers=ADMIN)
    assert client.get("/v1/user/credentials", headers=ADMIN).json() == []
    assert client.get("/v1/admin/credentials", headers=OTHER_ORG).json()["credentials"] == []


def test_only_administrators_manage_the_organizations_keys(client: TestClient) -> None:
    assert client.post("/v1/admin/credentials", json=_key(), headers=MEMBER).status_code == 403
    assert client.get("/v1/admin/credentials", headers=MEMBER).status_code == 403


def test_read_only_members_cannot_store_keys(client: TestClient) -> None:
    assert client.post("/v1/user/credentials", json=_key(), headers=READONLY).status_code == 403


@pytest.mark.asyncio
async def test_the_capability_decides_whether_members_bring_keys(client: TestClient) -> None:
    await save_org_config(None, "test_org", "access", {"bring_own_keys": ["owner", "admin"]}, [])
    assert client.post("/v1/user/credentials", json=_key(), headers=MEMBER).status_code == 403


def test_a_members_key_cannot_point_at_an_internal_endpoint(client: TestClient) -> None:
    internal = client.post(
        "/v1/user/credentials",
        json=_key("openai", api_base="http://169.254.169.254/v1"),
        headers=MEMBER,
    )
    assert internal.status_code == 422 and "api_base" in internal.json()["detail"]

    # An administrator may name their private vLLM/Ollama - that is the point of a base URL.
    private = client.post(
        "/v1/admin/credentials",
        json=_key("openai", api_base="http://vllm.internal:8000/v1"),
        headers=ADMIN,
    )
    assert (
        private.status_code == 200 and private.json()["api_base"] == "http://vllm.internal:8000/v1"
    )


def test_administrators_see_which_providers_the_deployment_holds_a_key_for(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in (
        "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
        "GEMINI_API_KEY", "AZURE_API_KEY", "MISTRAL_API_KEY", "GROQ_API_KEY",
    ):  # fmt: skip
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    monkeypatch.setenv("OPENAI_API_KEY", "   ")

    listed = client.get("/v1/admin/credentials/platform", headers=ADMIN)

    assert listed.status_code == 200
    assert [k["provider"] for k in listed.json()] == ["openrouter"]
    assert SECRET not in listed.text and "DO-NOT-LEAK" not in listed.text
    assert client.get("/v1/admin/credentials/platform", headers=MEMBER).status_code == 403


def test_spend_limits_replace_the_budget_settings(client: TestClient) -> None:
    assert client.put(
        "/v1/admin/budget", json={"monthly_budget_usd": 5}, headers=ADMIN
    ).status_code in (404, 405)
    res = client.put(
        "/v1/settings/budgets", json={"overrides": {"monthly_budget_usd": 1000000}}, headers=MEMBER
    )
    assert res.status_code in (404, 422)
