"""A model's context window comes from the best source that knows it, never from a silent guess.

A model the library has not heard of used to get 128K from the catalogue, which the context
manager accepted as an answer: its later, better-informed sources were never asked, and a
million-token model compacted at 110K tokens.
"""

from __future__ import annotations

import pytest
from agent_core.context import (
    DEFAULT_MODEL_CONTEXT_LIMITS,
    LIMIT_DEFAULT,
    LIMIT_FAMILY,
    LIMIT_OVERRIDE,
    LIMIT_PROVIDER,
    ContextManager,
    family_context_limit,
    threshold,
)
from api.services import model_catalogue
from api.services.model_catalogue import ModelCatalogue, resolve_model_spec
from api.services.provider_catalogue import (
    ProviderCatalogue,
    ProviderModel,
    parse_openrouter,
)

from specifications.interfaces.llm import ModelSpec

OPENROUTER_LISTING = {
    "data": [
        {
            "id": "xiaomi/mimo-v2.6-pro",
            "context_length": 1_050_000,
            "top_provider": {"max_completion_tokens": 131_072},
        },
        {"id": "acme/free-model", "context_length": 32_000, "top_provider": {}},
        {"id": "broken/no-window", "context_length": None},
    ]
}


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> ProviderCatalogue:
    catalogue = ProviderCatalogue()
    catalogue.load(parse_openrouter(OPENROUTER_LISTING))
    monkeypatch.setattr(model_catalogue, "provider_catalogue", catalogue)
    return catalogue


def test_the_listing_is_read_by_the_id_the_platform_routes_with(live: ProviderCatalogue) -> None:
    assert live.get("openrouter/xiaomi/mimo-v2.6-pro") == ProviderModel(1_050_000, 131_072)
    assert live.get("OpenRouter/Acme/Free-Model:free") == ProviderModel(32_000, None), (
        "a routing variant has its base model's window"
    )
    assert live.get("openrouter/broken/no-window") is None
    assert len(live) == 2


def test_a_failed_refresh_keeps_the_last_good_listing(live: ProviderCatalogue) -> None:
    live.load({})

    assert live.get("openrouter/xiaomi/mimo-v2.6-pro") is not None


def test_a_model_the_library_does_not_know_gets_the_providers_window(
    live: ProviderCatalogue,
) -> None:
    spec = resolve_model_spec("openrouter/xiaomi/mimo-v2.6-pro")

    assert (spec.context_window, spec.context_source) == (1_050_000, LIMIT_PROVIDER)
    manager = ContextManager(catalogue=ModelCatalogue())
    limit = manager.resolve_context_limit("openrouter/xiaomi/mimo-v2.6-pro")
    assert (limit.tokens, limit.source) == (1_050_000, LIMIT_PROVIDER)


def test_without_the_providers_listing_the_family_answers_before_the_default() -> None:
    manager = ContextManager(catalogue=ModelCatalogue())

    limit = manager.resolve_context_limit("openrouter/xiaomi/mimo-v9-unreleased")

    assert (limit.tokens, limit.source) == (1_000_000, LIMIT_FAMILY)


def test_a_model_nothing_knows_is_a_flagged_guess() -> None:
    manager = ContextManager(catalogue=ModelCatalogue())

    limit = manager.resolve_context_limit("custom/never-heard-of-it")

    assert (limit.tokens, limit.source) == (128_000, LIMIT_DEFAULT)


def test_the_organizations_own_size_and_a_registered_spec_win() -> None:
    catalogue = ModelCatalogue()
    catalogue.register(ModelSpec(id="custom/registered", context_window=77_000))
    manager = ContextManager(catalogue=catalogue)

    registered = manager.resolve_context_limit("custom/registered")
    override = manager.resolve_context_limit("custom/registered", [("custom/*", 50_000)])

    assert (registered.tokens, registered.source) == (77_000, LIMIT_OVERRIDE)
    assert (override.tokens, override.source) == (50_000, LIMIT_OVERRIDE)


def test_the_longest_matching_family_wins() -> None:
    assert family_context_limit("openrouter/openai/gpt-4.1-mini") == 1_000_000
    assert family_context_limit("openai/gpt-4o") == 128_000
    assert family_context_limit("nobody/nothing") is None


@pytest.mark.parametrize(
    "model",
    [
        "openrouter/xiaomi/mimo-v2.6-pro",
        "openrouter/moonshotai/kimi-k2",
        "openrouter/qwen/qwen3-coder",
        "openrouter/z-ai/glm-4.6",
        "openrouter/x-ai/grok-4",
        "openrouter/anthropic/claude-sonnet-5",
        "openrouter/google/gemini-3-pro",
        "openrouter/deepseek/deepseek-v4.1-flash",
    ],
)
def test_the_popular_families_are_never_left_to_the_default(model: str) -> None:
    assert family_context_limit(model) is not None
    assert any(key in model.lower() for key in DEFAULT_MODEL_CONTEXT_LIMITS)


def test_a_trigger_keeps_room_for_the_reply_without_compacting_a_small_window_constantly() -> None:
    assert threshold(128_000, 0.85) == 108_800, "no reserve: a share of the window"
    assert threshold(128_000, 0.85, 64_000) == 64_000, "never below half of the window"
    assert threshold(200_000, 0.9, 32_000) == 168_000, "the reply's room beats the share"
    assert threshold(1_000_000, 0.85, 16_000) == 850_000, "a large window is left to its share"
