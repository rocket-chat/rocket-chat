"""Deterministic per-model cost estimation shared by the real gateway and tests.

Kept as a module-level function rather than a method so routing tests can compute the
cost of a ledger's entries without instantiating a provider-backed gateway. The figures
are USD per million tokens; they mirror the rate cards of the model families the
platform routes to.
"""

from __future__ import annotations


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate USD cost for a model dispatch.

    Unknown models fall back to a conservative blended rate so a new model is never
    silently accounted as free.
    """
    name = (model or "").lower()
    if "claude-3-7" in name or "claude-3.7" in name:
        cost = (prompt_tokens * 3.0 / 1_000_000) + (completion_tokens * 15.0 / 1_000_000)
    elif "gpt-4o" in name:
        cost = (prompt_tokens * 2.5 / 1_000_000) + (completion_tokens * 10.0 / 1_000_000)
    elif "deepseek" in name:
        cost = (prompt_tokens * 0.14 / 1_000_000) + (completion_tokens * 0.28 / 1_000_000)
    elif "gemini-2.5" in name or "gemini-2.0" in name:
        cost = (prompt_tokens * 0.10 / 1_000_000) + (completion_tokens * 0.40 / 1_000_000)
    else:
        cost = (prompt_tokens * 1.0 / 1_000_000) + (completion_tokens * 3.0 / 1_000_000)
    return round(cost, 6)
