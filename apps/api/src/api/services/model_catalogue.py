"""The model picker: what a member may choose.

OpenRouter's catalogue is no longer the only source. The picker also lists the chat models
LiteLLM knows for every provider the caller can actually use (a platform key in the
environment, an organization key, or the member's own), plus the organization's custom models
(a private vLLM or Ollama). The organization's allow/deny patterns are applied to the whole
list, so the picker never offers a model the platform would then refuse.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from typing import Any

import litellm
from config_engine.cascading import model_is_allowed

from api.services.model_credentials import ModelCredentialStore
from api.services.settings import effective_settings

# Provider -> the environment variable holding the platform's key for it.
PLATFORM_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def _provider_label(provider: str) -> str:
    return {"openai": "OpenAI", "gemini": "Google", "deepseek": "DeepSeek"}.get(
        provider, provider.replace("_", " ").title()
    )


async def usable_providers(store: ModelCredentialStore, org_id: str, user_id: str) -> set[str]:
    """Providers the caller could call right now: environment, organization or own key."""
    providers = {p for p, env in PLATFORM_KEY_ENV.items() if os.getenv(env, "").strip()}
    providers.update(k.provider for k in await store.list(org_id, None))
    providers.update(k.provider for k in await store.list(org_id, user_id))
    return providers


def litellm_models(providers: Iterable[str]) -> list[dict[str, Any]]:
    """Chat models LiteLLM knows for ``providers``, with its context-window data."""
    wanted = set(providers) - {"openrouter"}  # OpenRouter has its own live catalogue
    found: list[dict[str, Any]] = []
    for name, info in litellm.model_cost.items():
        provider = info.get("litellm_provider")
        if provider not in wanted or info.get("mode") != "chat" or name == "sample_spec":
            continue
        model_id = name if name.startswith(f"{provider}/") else f"{provider}/{name}"
        found.append(
            {
                "id": model_id,
                "name": name.split("/", 1)[-1],
                "provider": _provider_label(str(provider)),
                "context_window": int(info.get("max_input_tokens") or 128_000),
                "recommended": False,
            }
        )
    return sorted(found, key=lambda m: (m["provider"], m["name"]))


def custom_models(entries: Sequence[str]) -> list[dict[str, Any]]:
    """The organization's own model ids (``ollama/llama3.1``, ``hosted_vllm/qwen2.5``)."""
    models = []
    for entry in entries:
        model_id = str(entry).strip()
        if not model_id:
            continue
        provider = model_id.split("/", 1)[0] if "/" in model_id else "custom"
        models.append(
            {
                "id": model_id,
                "name": model_id.split("/", 1)[-1],
                "provider": _provider_label(provider),
                "context_window": 128_000,
                "recommended": False,
                "custom": True,
            }
        )
    return models


async def build_catalogue(
    *,
    db_mgr: Any,
    store: ModelCredentialStore,
    org_id: str,
    user_id: str,
    openrouter: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """The deduplicated, policy-filtered list of models this caller may pick."""
    config = await effective_settings(db_mgr, org_id, "models")
    providers = await usable_providers(store, org_id, user_id)
    merged: dict[str, dict[str, Any]] = {}
    for model in [
        *custom_models(config.get("custom_models") or []),
        *openrouter,
        *litellm_models(providers),
    ]:
        merged.setdefault(model["id"], model)
    allowed = tuple(config.get("allowed_models") or ())
    forbidden = tuple(config.get("forbidden_models") or ())
    return [
        model
        for model in merged.values()
        if model_is_allowed(model["id"], allowed_models=allowed, forbidden_models=forbidden)
    ]
