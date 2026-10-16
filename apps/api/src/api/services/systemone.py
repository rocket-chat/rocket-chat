"""OpenRouter System One structured decision client.

System One models (such as `typesafe/jev-latest`, `typesafe/jev-1.14`, and `inception/mercury-decide`)
evaluate structured choices natively via OpenRouter's `/v1/systemone` endpoint.
They output calibrated probabilities with zero output token cost and millisecond latency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SYSTEMONE_ENDPOINT = "https://openrouter.ai/api/v1/systemone"

SYSTEMONE_MODEL_PREFIXES = (
    "typesafe/",
    "inception/mercury-decide",
)


def normalize_systemone_model(model: str) -> str:
    """Remove openrouter/ prefix if present."""
    clean = model.strip()
    if clean.lower().startswith("openrouter/"):
        return clean[len("openrouter/") :]
    return clean


def is_systemone_model(model: str) -> bool:
    """Return True if model targets an OpenRouter System One endpoint."""
    clean = normalize_systemone_model(model).lower()
    return clean.startswith(SYSTEMONE_MODEL_PREFIXES)


@dataclass(frozen=True)
class SystemOneChoiceResult:
    """The result of a choice question answered by a System One model."""

    choice: str
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)
    input_tokens: int = 0
    raw_response: dict[str, Any] = field(default_factory=dict)


def build_systemone_payload(
    model: str,
    message: str,
    thread_excerpt: str,
    question_key: str,
    instructions: str,
    criteria: dict[str, str],
) -> dict[str, Any]:
    """Format request payload for OpenRouter /v1/systemone."""
    clean_model = normalize_systemone_model(model)
    state: dict[str, Any] = {"user_message": message}
    if thread_excerpt.strip():
        state["thread_context"] = thread_excerpt.strip()

    return {
        "model": clean_model,
        "state": state,
        "questions": {
            question_key: {
                "type": "choice",
                "instructions": instructions,
                "criteria": criteria,
            }
        },
    }


def parse_systemone_response(data: dict[str, Any], question_key: str) -> SystemOneChoiceResult:
    """Parse System One response JSON into SystemOneChoiceResult."""
    answers = data.get("answers", {})
    route_ans = answers.get(question_key, {})
    choice = str(route_ans.get("choice") or "").strip()
    probs = {
        str(k): float(v)
        for k, v in (route_ans.get("probabilities") or {}).items()
        if isinstance(v, (int, float))
    }
    raw_conf = route_ans.get("confidence")
    if raw_conf is not None and isinstance(raw_conf, (int, float)):
        confidence = float(raw_conf)
    else:
        confidence = float(probs.get(choice, 1.0 if choice else 0.0))
    usage = data.get("usage", {})
    input_tokens = int(usage.get("input_tokens") or 0)

    return SystemOneChoiceResult(
        choice=choice,
        confidence=confidence,
        probabilities=probs,
        input_tokens=input_tokens,
        raw_response=data,
    )


async def call_systemone_choice(
    *,
    model: str,
    api_key: str,
    message: str,
    thread_excerpt: str = "",
    question_key: str = "route",
    instructions: str = "Select the appropriate route for this user request.",
    criteria: dict[str, str],
    api_endpoint: str = SYSTEMONE_ENDPOINT,
    timeout_seconds: float = 10.0,
) -> SystemOneChoiceResult:
    """Send a structured choice query to OpenRouter System One endpoint."""
    payload = build_systemone_payload(
        model, message, thread_excerpt, question_key, instructions, criteria
    )
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.post(api_endpoint, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    return parse_systemone_response(data, question_key)
