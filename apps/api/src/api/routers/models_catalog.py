"""The model catalogue the cockpit offers."""

import logging
import os
import time
from typing import Any, Literal

import httpx
from config_engine.model_defaults import (
    PROVIDER_PREFERENCE,
    PROVIDER_TIERS,
    deployment_default_model,
)
from fastapi import (
    APIRouter,
    Query,
    Request,
)
from llm_gateway.model_facts import facts_for

from api.auth.access import _get_request_identity
from api.auth.roles import is_admin
from api.routers.credentials import credential_store
from api.services.model_catalogue import build_catalogue
from api.services.provider_catalogue import parse_openrouter, provider_catalogue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Sessions & Telemetry"])


_models_cache: dict[str, Any] = {"timestamp": 0.0, "models": []}

_MODELS_CACHE_TTL = 3600.0  # 1 hour in-memory cache


_TIER_LABELS = {
    "default": "",
    "fast": " (fast)",
    "economy": " (economy)",
    "frontier": " (frontier)",
}


def _fallback_models(default_model: str) -> list[dict[str, Any]]:
    """The models the platform picks by default for each provider, for when no live listing
    answered: current models only, sized from ``model_facts``."""
    entries: dict[str, dict[str, Any]] = {}
    for provider in PROVIDER_PREFERENCE:
        tiers = PROVIDER_TIERS[provider]
        for tier in ("default", "frontier", "fast", "economy"):
            model = tiers.tier(tier)
            if model in entries:
                continue
            facts = facts_for(model)
            entries[model] = {
                "id": model,
                "name": f"{model.rsplit('/', 1)[-1]}{_TIER_LABELS[tier]}",
                "provider": provider.title() if provider != "openai" else "OpenAI",
                "context_window": facts.context_window if facts else 128_000,
                "recommended": model == default_model,
            }
    return list(entries.values())


def _popularity(default_model: str) -> dict[str, int]:
    """The order OpenRouter's listing is shown in: the platform's tier models first."""
    tiers = PROVIDER_TIERS["openrouter"]
    ranked = [default_model, *tiers.models()]
    return {model: rank for rank, model in enumerate(dict.fromkeys(ranked))}


@router.get("/models")
async def list_available_models(
    request: Request,
    scope: Literal["session", "agents"] = Query(
        "session",
        description=(
            "agents: the models an agent may be given, adding the providers of the "
            "organization's keys restricted to agents (administrators, who bind them)."
        ),
    ),
) -> dict[str, Any]:
    """Return available LLM models and sandbox execution driver status."""

    default_model = deployment_default_model()
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
                    listing = resp.json()
                    provider_catalogue.load(parse_openrouter(listing))
                    data = listing.get("data", [])
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
                        popular_rank = _popularity(default_model)
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
        models = _fallback_models(default_model)

    org_id, user_id, roles = _get_request_identity(request)
    try:
        catalogue = await build_catalogue(
            db_mgr=getattr(request.app.state, "db_manager", None),
            store=credential_store(request),
            org_id=org_id,
            user_id=user_id,
            openrouter=models,
            bindable=scope == "agents" and is_admin(roles),
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
