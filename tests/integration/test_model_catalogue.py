"""The model picker lists what the caller can use and respects the org's policy."""

from __future__ import annotations

import pytest
from api.services.model_catalogue import build_catalogue, custom_models, litellm_models
from api.services.model_credentials import ModelCredentialStore, NewCredential
from api.services.settings import reset_memory_settings, save_org_config

ORG, USER = "org_catalogue", "member_a"
OPENROUTER = [
    {
        "id": "openrouter/anthropic/claude-3.7-sonnet",
        "name": "Claude",
        "provider": "Anthropic",
        "context_window": 200000,
        "recommended": False,
    },
    {
        "id": "openrouter/openai/gpt-4o",
        "name": "GPT-4o",
        "provider": "OpenAI",
        "context_window": 128000,
        "recommended": False,
    },
]


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_memory_settings()
    for variable in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(variable, raising=False)


async def _ids(store: ModelCredentialStore | None = None) -> list[str]:
    models = await build_catalogue(
        db_mgr=None,
        store=store or ModelCredentialStore(None),
        org_id=ORG,
        user_id=USER,
        openrouter=OPENROUTER,
    )
    return [m["id"] for m in models]


@pytest.mark.asyncio
async def test_without_other_keys_only_openrouter_models_are_offered() -> None:
    assert await _ids() == [m["id"] for m in OPENROUTER]


@pytest.mark.asyncio
async def test_a_platform_key_adds_that_providers_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-platform")

    ids = await _ids()

    assert any(i.startswith("anthropic/claude") for i in ids)
    assert not any(i.startswith("openai/") or i.startswith("gemini/") for i in ids)


@pytest.mark.asyncio
async def test_a_members_or_organizations_key_adds_the_providers_models() -> None:
    store = ModelCredentialStore(None)
    await store.create(ORG, NewCredential("member", USER, "gemini", "gemini-key-12345678"))
    await store.create(ORG, NewCredential("org", None, "deepseek", "deepseek-key-12345678"))

    ids = await _ids(store)

    assert any(i.startswith("gemini/") for i in ids) and any(i.startswith("deepseek/") for i in ids)
    other_member = await build_catalogue(
        db_mgr=None, store=store, org_id=ORG, user_id="member_b", openrouter=OPENROUTER
    )
    assert not any(m["id"].startswith("gemini/") for m in other_member), (
        "personal keys stay personal"
    )


@pytest.mark.setting("models.custom_models")
@pytest.mark.asyncio
async def test_custom_models_for_a_private_endpoint_appear_first() -> None:
    await save_org_config(
        None,
        ORG,
        "models",
        {"custom_models": ["ollama/llama3.1", " hosted_vllm/qwen2.5-coder "]},
        [],
    )

    models = await build_catalogue(
        db_mgr=None,
        store=ModelCredentialStore(None),
        org_id=ORG,
        user_id=USER,
        openrouter=OPENROUTER,
    )

    assert [m["id"] for m in models[:2]] == ["ollama/llama3.1", "hosted_vllm/qwen2.5-coder"]
    assert all(m.get("custom") for m in models[:2])
    assert models[0]["provider"] == "Ollama"


@pytest.mark.asyncio
async def test_the_org_allow_and_deny_lists_filter_the_picker() -> None:
    await save_org_config(
        None,
        ORG,
        "models",
        {"allowed_models": ["openrouter/*"], "forbidden_models": ["*gpt-4o*"]},
        [],
    )

    assert await _ids() == ["openrouter/anthropic/claude-3.7-sonnet"]


def test_litellm_models_are_chat_models_with_their_context_window() -> None:
    models = litellm_models({"anthropic"})

    assert models and all(m["id"].startswith("anthropic/") for m in models)
    assert all(m["context_window"] > 0 for m in models)
    assert litellm_models({"openrouter"}) == [], "OpenRouter keeps its own live catalogue"


def test_custom_model_entries_are_cleaned() -> None:
    assert custom_models(["", "  ", "ollama/mistral"])[0]["id"] == "ollama/mistral"
    assert custom_models(["bare-model"])[0]["provider"] == "Custom"
