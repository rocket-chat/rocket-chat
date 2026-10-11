"""The model catalogue the cockpit offers."""

import logging
import os
import time
from typing import Any

import httpx
from fastapi import (
    APIRouter,
    Request,
)

from api.auth.access import _get_request_identity
from api.routers.model_keys import _model_key_store
from api.services.model_catalogue import build_catalogue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Sessions & Telemetry"])


_models_cache: dict[str, Any] = {"timestamp": 0.0, "models": []}

_MODELS_CACHE_TTL = 3600.0  # 1 hour in-memory cache


FALLBACK_MODELS = [
    {
        "id": "openrouter/deepseek/deepseek-v4.1-flash",
        "name": "DeepSeek V4.1 Flash",
        "provider": "DeepSeek",
        "context_window": 131072,
        "recommended": True,
    },
    {
        "id": "openrouter/anthropic/claude-3.7-sonnet",
        "name": "Claude 3.7 Sonnet (Hybrid)",
        "provider": "Anthropic",
        "context_window": 200000,
        "recommended": False,
    },
    {
        "id": "openrouter/anthropic/claude-3.5-sonnet",
        "name": "Claude 3.5 Sonnet",
        "provider": "Anthropic",
        "context_window": 200000,
        "recommended": False,
    },
    {
        "id": "openrouter/openai/gpt-4o",
        "name": "GPT-4o Omnimodal",
        "provider": "OpenAI",
        "context_window": 128000,
        "recommended": False,
    },
    {
        "id": "openrouter/openai/gpt-4o-mini",
        "name": "GPT-4o Mini (Fast)",
        "provider": "OpenAI",
        "context_window": 128000,
        "recommended": False,
    },
]


@router.get("/models")
async def list_available_models(request: Request) -> dict[str, Any]:
    """Return available LLM models and sandbox execution driver status."""

    default_model = os.getenv("ROCKET_DEFAULT_MODEL", "openrouter/deepseek/deepseek-v4.1-flash")
    driver_type = getattr(request.app.state, "sandbox_driver", None)
    driver_name = "k8s" if driver_type and hasattr(driver_type, "namespace") else "docker"

    now = time.time()
    models: list[dict[str, Any]] = _models_cache.get("models", [])

    if not models or (now - _models_cache.get("timestamp", 0.0) > _MODELS_CACHE_TTL):
        try:
            api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
            headers = {}
            if api_key:
                clean_key = "".join(ch for ch in api_key if ch not in "\r\n\x00").strip()
                headers["Authorization"] = f"Bearer {clean_key}"
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get("https://openrouter.ai/api/v1/models", headers=headers)
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    parsed: list[dict[str, Any]] = []
                    for item in data:
                        raw_id = item.get("id", "")
                        if not raw_id:
                            continue
                        model_id = f"openrouter/{raw_id}"
                        provider_part = (
                            raw_id.split("/")[0].replace("-", " ").title()
                            if "/" in raw_id
                            else "OpenRouter"
                        )
                        context_len = int(item.get("context_length") or 128000)
                        parsed.append(
                            {
                                "id": model_id,
                                "name": item.get("name") or raw_id,
                                "provider": provider_part,
                                "context_window": context_len,
                                "recommended": model_id == default_model,
                            }
                        )
                    if parsed:
                        popular_rank = {
                            "openrouter/deepseek/deepseek-v4.1-flash": 0,
                            "openrouter/anthropic/claude-3.7-sonnet": 1,
                            "openrouter/anthropic/claude-3.5-sonnet": 2,
                            "openrouter/openai/gpt-4o": 3,
                            "openrouter/openai/gpt-4o-mini": 4,
                            "openrouter/deepseek/deepseek-chat": 5,
                        }
                        parsed.sort(
                            key=lambda m: (
                                popular_rank.get(m["id"], 999),
                                not m["recommended"],
                                m["name"],
                            )
                        )
                        _models_cache["models"] = parsed
                        _models_cache["timestamp"] = now
                        models = parsed
        except Exception as err:
            logger.warning("Could not fetch dynamic OpenRouter models: %s", err)

    if not models:
        models = FALLBACK_MODELS

    org_id, user_id, _ = _get_request_identity(request)
    try:
        catalogue = await build_catalogue(
            db_mgr=getattr(request.app.state, "db_manager", None),
            store=_model_key_store(request),
            org_id=org_id,
            user_id=user_id,
            openrouter=models,
        )
    except Exception:
        # The picker is a convenience: the allow/deny policy is enforced again when a turn
        # resolves its model, so a settings or key-store hiccup must not take the list down.
        logger.exception("Could not build the personalised model catalogue")
        catalogue = models
    return {
        "default_model": default_model,
        "active_driver": driver_name,
        "models": catalogue,
    }
