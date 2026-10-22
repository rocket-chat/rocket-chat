"""Model limits from the provider's own live catalogue.

LiteLLM's table is a release behind for new models: a model it does not list used to fall back to
a 128K window and a context that compacted at 110K tokens although the provider serves a million.
OpenRouter publishes every model's ``context_length`` and ``max_completion_tokens``, so the
platform reads them from there and keeps the last good answer when the fetch fails.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_PREFIX = "openrouter/"
FETCH_TIMEOUT_SECONDS = 10.0
REFRESH_SECONDS = 6 * 3600


@dataclass(frozen=True)
class ProviderModel:
    """What the provider says a model can take and write."""

    context_window: int
    max_output_tokens: int | None


def _positive(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) and value > 0 else None


def parse_openrouter(payload: Mapping[str, Any]) -> dict[str, ProviderModel]:
    """``{"openrouter/<id>": ProviderModel}`` for every model of an OpenRouter listing."""
    parsed: dict[str, ProviderModel] = {}
    for item in payload.get("data") or []:
        model_id = str(item.get("id") or "").strip().lower()
        context = _positive(item.get("context_length"))
        if not model_id or context is None:
            continue
        top = item.get("top_provider") or {}
        parsed[f"{OPENROUTER_PREFIX}{model_id}"] = ProviderModel(
            context_window=context,
            max_output_tokens=_positive(top.get("max_completion_tokens")),
        )
    return parsed


class ProviderCatalogue:
    """The provider limits last fetched, looked up by the id the platform routes with."""

    def __init__(self) -> None:
        self._models: dict[str, ProviderModel] = {}
        self.refreshed_at = 0.0

    def get(self, model: str) -> ProviderModel | None:
        name = (model or "").strip().lower()
        found = self._models.get(name)
        if found is None and ":" in name:
            # A routing variant (":free", ":nitro") has the base model's window.
            found = self._models.get(name.split(":", 1)[0])
        return found

    def load(self, models: Mapping[str, ProviderModel]) -> None:
        if models:
            self._models = dict(models)
            self.refreshed_at = time.time()

    def __len__(self) -> int:
        return len(self._models)

    async def refresh(self) -> int:
        """Fetch OpenRouter's listing; the models known afterwards (the old ones on failure)."""
        headers = {}
        if key := os.getenv("OPENROUTER_API_KEY", "").strip():
            headers["Authorization"] = f"Bearer {key}"
        try:
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS) as client:
                response = await client.get(OPENROUTER_MODELS_URL, headers=headers)
                response.raise_for_status()
                self.load(parse_openrouter(response.json()))
        except (httpx.HTTPError, ValueError) as err:
            logger.warning("Could not refresh the provider model catalogue: %s", err)
        return len(self._models)


provider_catalogue = ProviderCatalogue()
