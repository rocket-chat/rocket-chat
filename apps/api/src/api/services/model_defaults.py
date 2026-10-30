"""An organization's default models, from the keys it can use (``config_engine.model_defaults``).

The deployment's default (``ROCKET_DEFAULT_MODEL``, else the default for the environment's keys)
holds unless the organization chose a default provider (``models.default_provider``) or stored
keys for providers the deployment has none for: then the default comes from the providers the
organization can actually call, Anthropic first.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from config_engine.model_defaults import (
    PROVIDER_TIERS,
    model_tiers_for,
    providers_from_environment,
    tier_model,
)

from api.services.model_credentials import ModelCredentialStore

logger = logging.getLogger(__name__)

# Explicit organization settings that replace a derived tier.
_TIER_SETTINGS = {"fast": "fast_model", "economy": "economy_model", "frontier": "frontier_model"}


async def organization_credential_providers(
    db_mgr: Any, org_id: str, store: ModelCredentialStore | None = None
) -> frozenset[str]:
    """Providers the organization holds a shared key for (not keys restricted to agents)."""
    credentials = store or ModelCredentialStore(db_mgr)
    try:
        listed = await credentials.list(org_id, owner_kind="org")
    except Exception:
        # A default is a convenience: an unreadable key store must not stop the turn, which
        # then runs on the deployment's default.
        logger.warning("Could not list the provider keys of %s", org_id, exc_info=True)
        return frozenset()
    return frozenset(c.provider for c in listed if not c.restricted_to_agents)


async def organization_providers(
    db_mgr: Any, org_id: str, store: ModelCredentialStore | None = None
) -> frozenset[str]:
    """Providers with a key the organization's turns can use: the deployment's and its own."""
    return providers_from_environment() | await organization_credential_providers(
        db_mgr, org_id, store
    )


async def organization_default_model(
    db_mgr: Any,
    org_id: str,
    models: Mapping[str, Any],
    deployment_default: str,
    store: ModelCredentialStore | None = None,
) -> str:
    """The default model below the organization's own ``default_model`` setting."""
    preferred = str(models.get("default_provider") or "")
    environment = providers_from_environment()
    own = await organization_credential_providers(db_mgr, org_id, store)
    available = environment | own
    if preferred in PROVIDER_TIERS and preferred in available:
        return model_tiers_for(available, preferred).default
    if own - environment:
        return model_tiers_for(available).default
    return deployment_default


def model_for_tier(tier: str, base_model: str, models: Mapping[str, Any]) -> str:
    """The organization's model for ``tier``: its explicit setting, else that tier of the
    provider serving ``base_model`` (same key), else ``base_model``."""
    setting = _TIER_SETTINGS.get(tier)
    if setting is None:
        return base_model
    explicit = str(models.get(setting) or "").strip()
    return explicit or tier_model(tier, base_model)
