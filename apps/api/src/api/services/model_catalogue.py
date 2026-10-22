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
from dataclasses import replace
from typing import Any, cast

import litellm
from agent_core.context import (
    FALLBACK_CONTEXT_LIMIT,
    LIMIT_DEFAULT,
    LIMIT_FAMILY,
    LIMIT_LITELLM,
    LIMIT_OVERRIDE,
    LIMIT_PROVIDER,
    family_context_limit,
)
from config_engine.cascading import model_is_allowed

from api.services.model_credentials import ModelCredentialStore
from api.services.provider_catalogue import provider_catalogue
from api.services.settings import effective_settings
from specifications.interfaces.llm import (
    ModelCatalogueProtocol,
    ModelSpec,
    is_reasoning_model,
)

# Provider -> the environment variable holding the platform's key for it.
PLATFORM_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

REASONING_OUTPUT_CEILING = 64_000
DEFAULT_MAX_OUTPUT = 8_192


def _lookup_litellm_info(model: str) -> dict[str, Any] | None:
    """Look up model info from litellm, trying full name then base name."""
    if model in litellm.model_cost:
        return cast(dict[str, Any], litellm.model_cost[model])
    base = model.split("/", 1)[-1]
    if base in litellm.model_cost:
        return cast(dict[str, Any], litellm.model_cost[base])
    if "/" in model:
        stripped = model.split("/")[-1]
        if stripped in litellm.model_cost:
            return cast(dict[str, Any], litellm.model_cost[stripped])
    return None


def resolve_model_spec(model: str) -> ModelSpec:
    """Derive context window, max output tokens, and reasoning support for a model.

    The window comes from the provider's live catalogue first, then LiteLLM's table, then the
    model's family; ``context_source`` says which, and is ``default`` only when none knew it.
    """
    clean_id = (model or "").strip()
    reasoning = is_reasoning_model(clean_id)
    info = _lookup_litellm_info(clean_id)
    live = provider_catalogue.get(clean_id)

    context_window = FALLBACK_CONTEXT_LIMIT
    context_source = LIMIT_DEFAULT
    max_output = REASONING_OUTPUT_CEILING if reasoning else DEFAULT_MAX_OUTPUT
    provider = clean_id.split("/")[0] if "/" in clean_id else ""

    if info:
        provider = str(info.get("litellm_provider") or provider)
        ctx = info.get("max_input_tokens") or info.get("max_tokens")
        if isinstance(ctx, (int, float)) and ctx > 0:
            context_window, context_source = int(ctx), LIMIT_LITELLM
        out = info.get("max_output_tokens") or info.get("max_tokens")
        if isinstance(out, (int, float)) and out > 0:
            max_output = int(out)
    if context_source == LIMIT_DEFAULT and (family := family_context_limit(clean_id)):
        context_window, context_source = family, LIMIT_FAMILY
    if live is not None:
        context_window, context_source = live.context_window, LIMIT_PROVIDER
        if live.max_output_tokens:
            max_output = live.max_output_tokens

    if reasoning:
        # Standard reasoning ceiling: up to 64k, bounded if provider catalog explicitly has lower
        max_output_tokens = REASONING_OUTPUT_CEILING
        if max_output > 8_192 and max_output < REASONING_OUTPUT_CEILING:
            max_output_tokens = max_output
    else:
        max_output_tokens = min(max_output, 16_384) if max_output > 0 else DEFAULT_MAX_OUTPUT

    return ModelSpec(
        id=clean_id,
        name=clean_id.split("/")[-1],
        provider=_provider_label(provider),
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        supports_reasoning=reasoning,
        context_source=context_source,
    )


class ModelCatalogue(ModelCatalogueProtocol):
    """Protocol-compliant catalogue service for model capabilities and budgets."""

    def __init__(self) -> None:
        self._overrides: dict[str, ModelSpec] = {}

    def register(self, spec: ModelSpec) -> None:
        # What an operator registers is not a guess, whatever the spec says about its source.
        self._overrides[spec.id] = replace(spec, context_source=LIMIT_OVERRIDE)

    def get_model_spec(self, model: str) -> ModelSpec:
        if model in self._overrides:
            return self._overrides[model]
        return resolve_model_spec(model)


default_catalogue = ModelCatalogue()


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
