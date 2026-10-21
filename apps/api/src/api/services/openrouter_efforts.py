"""The reasoning efforts each OpenRouter model accepts.

OpenRouter's catalogue lists, per model, the effort levels its upstream takes and the one it
uses when none is sent. The levels differ model to model (``deepseek-v4-pro-0813`` takes
``max``, ``high``, ``low`` and ``none``, never ``medium``), and an effort the upstream does not
take is rounded to something else without telling anyone. The platform therefore maps the
effort an agent or an organization asks for onto one the model takes before the request leaves.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CATALOGUE_URL = "https://openrouter.ai/api/v1/models"
CACHE_SECONDS = 3600.0
# A failed fetch is not retried on every turn: the platform falls back to the effort as asked.
FAILURE_RETRY_SECONDS = 300.0
FETCH_TIMEOUT_SECONDS = 4.0

# Cheapest first; ``none`` turns reasoning off.
EFFORT_LADDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class ReasoningSupport:
    """What one model accepts, as OpenRouter's catalogue lists it."""

    efforts: tuple[str, ...]
    default_effort: str | None = None


def map_effort(requested: str, support: ReasoningSupport | None) -> str:
    """``requested`` if the model takes it, else the nearest level it does take.

    A tie rounds down: a level between two the model takes costs less at the lower one, and
    ``none`` is never chosen for a request that wanted some reasoning.
    """
    if support is None or not support.efforts or requested in support.efforts:
        return requested
    if requested not in EFFORT_LADDER:
        return requested
    wanted = EFFORT_LADDER.index(requested)
    candidates = [e for e in support.efforts if e in EFFORT_LADDER]
    if requested != "none":
        candidates = [e for e in candidates if e != "none"] or candidates
    if not candidates:
        return requested
    return min(
        candidates, key=lambda e: (abs(EFFORT_LADDER.index(e) - wanted), EFFORT_LADDER.index(e))
    )


def parse_catalogue(data: list[dict[str, Any]]) -> dict[str, ReasoningSupport]:
    """Per model id (``openrouter/<id>``), the efforts its catalogue entry lists."""
    parsed: dict[str, ReasoningSupport] = {}
    for item in data:
        reasoning = item.get("reasoning")
        raw_id = item.get("id")
        if not raw_id or not isinstance(reasoning, dict):
            continue
        efforts = tuple(str(e) for e in reasoning.get("supported_efforts") or ())
        if efforts:
            default = reasoning.get("default_effort")
            parsed[f"openrouter/{raw_id}"] = ReasoningSupport(
                efforts, str(default) if default else None
            )
    return parsed


class OpenRouterEfforts:
    """A cached view of the catalogue's reasoning data, fetched on first use."""

    def __init__(self) -> None:
        self._support: dict[str, ReasoningSupport] = {}
        self._expires = 0.0
        self._lock = asyncio.Lock()

    async def support_for(self, model: str) -> ReasoningSupport | None:
        if not model.startswith("openrouter/"):
            return None
        if time.monotonic() >= self._expires:
            await self._refresh()
        return self._support.get(model)

    async def _refresh(self) -> None:
        async with self._lock:
            if time.monotonic() < self._expires:
                return
            try:
                self._support = parse_catalogue(await _fetch_catalogue())
                self._expires = time.monotonic() + CACHE_SECONDS
            except (httpx.HTTPError, ValueError) as err:
                logger.warning("Could not read OpenRouter's reasoning efforts: %s", err)
                self._expires = time.monotonic() + FAILURE_RETRY_SECONDS


async def _fetch_catalogue() -> list[dict[str, Any]]:
    headers = {}
    if key := "".join(ch for ch in os.getenv("OPENROUTER_API_KEY", "") if ch.isprintable()).strip():
        headers["Authorization"] = f"Bearer {key}"
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS) as client:
        response = await client.get(CATALOGUE_URL, headers=headers)
        response.raise_for_status()
        data = response.json().get("data", [])
    return [item for item in data if isinstance(item, dict)]


openrouter_efforts = OpenRouterEfforts()
