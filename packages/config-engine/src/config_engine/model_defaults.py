"""Default models chosen by the provider keys a deployment or an organization has.

A deployment with only an Anthropic key used to default to an OpenRouter model it could not
call. Every default the platform picks (the default model, the fast and economy models for
auxiliary work, the frontier model for planning, fail-over models and the embedding model) is
derived here from the providers that hold a key, so there is one table to keep current.

When several providers hold a key the first in ``PROVIDER_PREFERENCE`` wins (Anthropic first),
unless the organization names its own default provider (``models.default_provider``).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

# Provider -> the environment variables that may hold the deployment's key for it.
PLATFORM_KEY_ENV: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "deepseek": ("DEEPSEEK_API_KEY",),
}

PROVIDER_PREFERENCE: tuple[str, ...] = ("anthropic", "openrouter", "openai", "gemini", "deepseek")

TIER_NAMES: tuple[str, ...] = ("default", "fast", "economy", "frontier")
# An agent may name a tier instead of a model (``tier:frontier``): it resolves to the
# organization's model for that tier, so a built-in agent works with whichever provider is set up.
TIER_ALIAS_PREFIX = "tier:"


@dataclass(frozen=True)
class ModelTiers:
    """One provider's model for each kind of work."""

    provider: str
    default: str
    fast: str
    economy: str
    frontier: str

    def tier(self, name: str) -> str:
        return {
            "default": self.default,
            "fast": self.fast,
            "economy": self.economy,
            "frontier": self.frontier,
        }.get(name, self.default)

    def models(self) -> tuple[str, ...]:
        return (self.default, self.fast, self.economy, self.frontier)


# Checked against each provider's own model listing (Anthropic, OpenAI, Google) and LiteLLM's
# model table in October 2026. OpenRouter spells Anthropic versions with a dot.
PROVIDER_TIERS: dict[str, ModelTiers] = {
    "anthropic": ModelTiers(
        provider="anthropic",
        default="anthropic/claude-sonnet-5-5",
        fast="anthropic/claude-haiku-5-5",
        economy="anthropic/claude-haiku-5-5",
        frontier="anthropic/claude-opus-5-5",
    ),
    "openrouter": ModelTiers(
        provider="openrouter",
        default="openrouter/anthropic/claude-sonnet-5.5",
        fast="openrouter/anthropic/claude-haiku-5.5",
        economy="openrouter/deepseek/deepseek-v4.1-flash",
        frontier="openrouter/anthropic/claude-opus-5.5",
    ),
    "openai": ModelTiers(
        provider="openai",
        default="openai/gpt-6.1-sol",
        fast="openai/gpt-6-luna",
        economy="openai/gpt-6-luna",
        frontier="openai/gpt-6-astra",
    ),
    "gemini": ModelTiers(
        provider="gemini",
        default="gemini/gemini-3.8-flash",
        fast="gemini/gemini-3.5-flash-lite",
        economy="gemini/gemini-3.5-flash-lite",
        frontier="gemini/gemini-4-argon",
    ),
    "deepseek": ModelTiers(
        provider="deepseek",
        default="deepseek/deepseek-v4-pro",
        fast="deepseek/deepseek-v4-flash",
        economy="deepseek/deepseek-v4-flash",
        frontier="deepseek/deepseek-v4-pro",
    ),
}

# Without any key the platform still needs a model name to report; OpenRouter is the one key
# that reaches every family, so its tiers are the ones a new deployment is told to configure.
NO_PROVIDER = "openrouter"

# Embedding models by provider, all producing the 1536 dimensions the vector columns hold.
# Anthropic, OpenRouter and DeepSeek offer none, so a deployment with only those keys has no
# semantic recall (see ``embedding_model_for``).
EMBEDDING_DIMENSIONS = 1536
PROVIDER_EMBEDDING_MODELS: dict[str, str] = {
    "openai": "text-embedding-3-small",
    "gemini": "gemini/gemini-embedding-001",
}


def provider_of(model: str) -> str:
    """The provider prefix of a LiteLLM model id (``anthropic/claude-…`` -> ``anthropic``)."""
    name = (model or "").strip().lower()
    return name.split("/", 1)[0] if "/" in name else ""


def providers_from_environment(environ: Mapping[str, str] | None = None) -> frozenset[str]:
    """Providers whose key the deployment holds in its environment."""
    source = os.environ if environ is None else environ
    return frozenset(
        provider
        for provider, names in PLATFORM_KEY_ENV.items()
        if any((source.get(name) or "").strip() for name in names)
    )


def preferred_provider(available: Iterable[str], preferred: str = "") -> str:
    """The provider whose models are the defaults: ``preferred`` when it has a key, else the
    first available in ``PROVIDER_PREFERENCE``, else ``NO_PROVIDER``."""
    present = set(available)
    choice = (preferred or "").strip().lower()
    if choice in PROVIDER_TIERS and choice in present:
        return choice
    return next((p for p in PROVIDER_PREFERENCE if p in present), NO_PROVIDER)


def model_tiers_for(available: Iterable[str], preferred: str = "") -> ModelTiers:
    """The tiers of the provider ``preferred_provider`` picks."""
    return PROVIDER_TIERS[preferred_provider(available, preferred)]


def default_model_for(available: Iterable[str], preferred: str = "") -> str:
    """The default model for a deployment or organization holding keys for ``available``."""
    return model_tiers_for(available, preferred).default


def tiers_of_model(model: str) -> ModelTiers | None:
    """The tiers of the provider serving ``model``, when the platform knows it."""
    return PROVIDER_TIERS.get(provider_of(model))


def tier_model(tier: str, model: str) -> str:
    """The model of ``tier`` from the same provider as ``model`` (so it uses the same key);
    ``model`` itself when the provider is not one the platform has tiers for."""
    tiers = tiers_of_model(model)
    return tiers.tier(tier) if tiers is not None else (model or "").strip()


def tier_alias(model: str | None) -> str | None:
    """The tier named by ``tier:<name>``, else ``None``."""
    name = (model or "").strip().lower()
    if not name.startswith(TIER_ALIAS_PREFIX):
        return None
    tier = name[len(TIER_ALIAS_PREFIX) :]
    return tier if tier in TIER_NAMES else None


def fallback_models_for(model: str, available: Iterable[str], limit: int = 2) -> tuple[str, ...]:
    """The same tier through other providers that hold a key, for fail-over.

    Only for a model the table knows: its tier is unambiguous there, and an unknown model has
    no equivalent the platform could vouch for.
    """
    tiers = tiers_of_model(model)
    if tiers is None:
        return ()
    clean = model.strip().lower()
    tier = next((name for name in TIER_NAMES if tiers.tier(name) == clean), None)
    if tier is None:
        return ()
    present = set(available)
    others = [
        PROVIDER_TIERS[p].tier(tier)
        for p in PROVIDER_PREFERENCE
        if p in present and p != tiers.provider
    ]
    return tuple(dict.fromkeys(m for m in others if m != clean))[: max(0, limit)]


def embedding_model_for(available: Iterable[str]) -> str:
    """The embedding model of the first provider that offers one; empty when none does."""
    present = set(available)
    return next(
        (PROVIDER_EMBEDDING_MODELS[p] for p in PROVIDER_EMBEDDING_MODELS if p in present), ""
    )


def deployment_default_model(environ: Mapping[str, str] | None = None) -> str:
    """The operator's ``ROCKET_DEFAULT_MODEL``, else the default for the keys in the environment."""
    source = os.environ if environ is None else environ
    for name in ("ROCKET_DEFAULT_MODEL", "DEFAULT_MODEL"):
        if value := (source.get(name) or "").strip():
            return value
    return default_model_for(providers_from_environment(source))
