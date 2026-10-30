"""What the platform knows about the models it picks by default, when nothing better is known.

LiteLLM's model table (``litellm.model_cost``) and the providers' live listings are read first;
this table covers the models ``config_engine.model_defaults`` chooses, so a default the table
of the installed LiteLLM does not list yet is still priced and sized correctly. Prices are USD
per million tokens; ``tests/unit/test_model_facts.py`` fails when a default model is missing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelFacts:
    context_window: int
    max_output_tokens: int
    input_usd: float
    output_usd: float
    cache_read_usd: float
    cache_write_usd: float
    reasoning: bool = True


def _anthropic(input_usd: float, output_usd: float, window: int = 200_000) -> ModelFacts:
    # Anthropic bills cache reads at a tenth of the input price and 5-minute cache writes at 1.25x.
    return ModelFacts(window, 64_000, input_usd, output_usd, input_usd * 0.1, input_usd * 1.25)


def _cached_at_tenth(window: int, output: int, input_usd: float, output_usd: float) -> ModelFacts:
    return ModelFacts(window, output, input_usd, output_usd, input_usd * 0.1, input_usd)


# Keys are the model's own name, lower-case, with dots written as dashes (``normalised_name``).
_FACTS: dict[str, ModelFacts] = {
    "claude-opus-5-5": _anthropic(5.0, 25.0),
    "claude-sonnet-5-5": _anthropic(3.0, 15.0, window=1_000_000),
    "claude-haiku-5-5": _anthropic(1.0, 5.0),
    "gpt-6-astra": _cached_at_tenth(400_000, 128_000, 15.0, 120.0),
    "gpt-6-1-sol": _cached_at_tenth(400_000, 128_000, 1.25, 10.0),
    "gpt-6-luna": _cached_at_tenth(400_000, 128_000, 0.25, 2.0),
    "gemini-4-argon": _cached_at_tenth(1_048_576, 65_536, 2.5, 15.0),
    "gemini-3-8-flash": _cached_at_tenth(1_048_576, 65_536, 0.3, 2.5),
    "gemini-3-5-flash-lite": _cached_at_tenth(1_048_576, 65_536, 0.1, 0.4),
    "deepseek-v4-pro": _cached_at_tenth(1_048_576, 64_000, 0.55, 2.19),
    "deepseek-v4-flash": _cached_at_tenth(1_048_576, 64_000, 0.14, 0.28),
    "deepseek-v4-1-flash": _cached_at_tenth(1_048_576, 64_000, 0.14, 0.28),
}

# A model the table does not name is priced by its family, most specific first.
_FAMILY_PRICES: tuple[tuple[str, tuple[float, float, float, float]], ...] = (
    ("claude-opus", (5.0, 25.0, 0.5, 6.25)),
    ("claude-haiku", (1.0, 5.0, 0.1, 1.25)),
    ("claude", (3.0, 15.0, 0.3, 3.75)),
    ("gpt-4o-mini", (0.15, 0.6, 0.075, 0.15)),
    ("gpt-4o", (2.5, 10.0, 1.25, 2.5)),
    ("deepseek", (0.14, 0.28, 0.014, 0.14)),
    ("flash-lite", (0.1, 0.4, 0.025, 0.1)),
    ("gemini", (0.3, 2.5, 0.03, 0.3)),
)
# Unknown models are never accounted as free.
UNKNOWN_PRICES = (1.0, 3.0, 0.5, 1.0)


def normalised_name(model: str) -> str:
    """``openrouter/anthropic/claude-sonnet-5.5:nitro`` -> ``claude-sonnet-5-5``."""
    name = (model or "").strip().lower().rsplit("/", 1)[-1].split(":", 1)[0]
    return name.replace(".", "-")


def facts_for(model: str) -> ModelFacts | None:
    return _FACTS.get(normalised_name(model))


def prices_for(model: str) -> tuple[float, float, float, float]:
    """(input, output, cache read, cache write) USD per million tokens."""
    if facts := facts_for(model):
        return facts.input_usd, facts.output_usd, facts.cache_read_usd, facts.cache_write_usd
    name = normalised_name(model)
    return next((prices for family, prices in _FAMILY_PRICES if family in name), UNKNOWN_PRICES)


def known_models() -> tuple[str, ...]:
    return tuple(_FACTS)
