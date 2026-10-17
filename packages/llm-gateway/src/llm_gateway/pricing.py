"""Deterministic per-model cost estimation shared by the real gateway and tests.

Kept as a module-level function rather than a method so routing tests can compute the
cost of a ledger's entries without instantiating a provider-backed gateway. The figures
are USD per million tokens; they mirror the rate cards of the model families the
platform routes to.
"""

from __future__ import annotations


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
    name = (model or "").lower()
    if "claude" in name:
        p_rate, c_rate, cr_rate, cw_rate = 3.0, 15.0, 0.30, 3.75
    elif "gpt-4o" in name:
        p_rate, c_rate, cr_rate, cw_rate = 2.5, 10.0, 1.25, 2.5
    elif "deepseek" in name:
        p_rate, c_rate, cr_rate, cw_rate = 0.14, 0.28, 0.014, 0.14
    elif "gemini-2.5" in name or "gemini-2.0" in name:
        p_rate, c_rate, cr_rate, cw_rate = 0.10, 0.40, 0.025, 0.10
    else:
        p_rate, c_rate, cr_rate, cw_rate = 1.0, 3.0, 0.50, 1.0

    uncached_prompt = max(0, prompt_tokens - cache_read_tokens - cache_creation_tokens)
    cost = (
        (uncached_prompt * p_rate / 1_000_000)
        + (cache_read_tokens * cr_rate / 1_000_000)
        + (cache_creation_tokens * cw_rate / 1_000_000)
        + (completion_tokens * c_rate / 1_000_000)
    )
    return round(cost, 6)
