"""Deterministic per-model cost estimation shared by the real gateway and tests.

Kept as a module-level function rather than a method so routing tests can compute the
cost of a ledger's entries without instantiating a provider-backed gateway. The figures
are USD per million tokens, from ``model_facts`` (the default models by name, others by
family).
"""

from __future__ import annotations

from llm_gateway.model_facts import prices_for


def estimate_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Estimate USD cost for a model dispatch accounting for prompt caching.

    Unknown models fall back to a conservative blended rate so a new model is never
    silently accounted as free.
    """
    p_rate, c_rate, cr_rate, cw_rate = prices_for(model)

    uncached_prompt = max(0, prompt_tokens - cache_read_tokens - cache_creation_tokens)
    cost = (
        (uncached_prompt * p_rate / 1_000_000)
        + (cache_read_tokens * cr_rate / 1_000_000)
        + (cache_creation_tokens * cw_rate / 1_000_000)
        + (completion_tokens * c_rate / 1_000_000)
    )
    return round(cost, 6)
