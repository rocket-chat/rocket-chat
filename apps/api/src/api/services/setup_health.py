"""Setup health: what a new deployment or organization is missing, each with where to fix it.

Read-only checks an administrator sees as one card: a provider key and a default model it can
call, embeddings for semantic recall, a web search provider other than scraping, the GitHub App,
and whether the sandbox images are already on the host.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Literal

from agent_core.tools.search_providers import configured_provider_name
from config_engine.model_defaults import provider_of

from api.services.github_installations import installations_for_org
from api.services.memory import configured_embedding_model
from api.services.model_defaults import organization_default_model, organization_providers
from api.services.sandbox_catalogue import SandboxCatalogue
from api.services.settings import effective_settings, load_org_config

logger = logging.getLogger(__name__)

Status = Literal["ok", "warning", "error", "unknown"]
DOCS = "https://docs.rocket-chat.dev"


@dataclass(frozen=True)
class HealthCheck:
    id: str
    title: str
    status: Status
    detail: str
    fix_label: str = ""
    fix_href: str = ""


def _model_reachable(model: str, providers: frozenset[str]) -> bool:
    provider = provider_of(model)
    return provider in providers or (provider == "" and bool(providers))


async def _model_check(db_mgr: Any, org_id: str, deployment_default: str) -> HealthCheck:
    providers = await organization_providers(db_mgr, org_id)
    models = await effective_settings(db_mgr, org_id, "models")
    default = str(models.get("default_model") or "") or await organization_default_model(
        db_mgr, org_id, models, deployment_default
    )
    fix = ("Add a provider key", "/settings/org/models")
    if not providers:
        return HealthCheck(
            "models", "Model provider", "error", "No provider key: no turn can call a model.", *fix
        )
    if not _model_reachable(default, providers):
        return HealthCheck(
            "models",
            "Model provider",
            "error",
            f"The default model {default} needs a {provider_of(default)} key, which is missing.",
            *fix,
        )
    keys = ", ".join(sorted(providers))
    return HealthCheck("models", "Model provider", "ok", f"Default model {default} (keys: {keys}).")


async def _embedding_check(db_mgr: Any, org_id: str) -> HealthCheck:
    memory, _ = await load_org_config(db_mgr, org_id, "memory")
    model = configured_embedding_model(memory)
    if model:
        return HealthCheck("embeddings", "Semantic recall", "ok", f"Embeddings from {model}.")
    return HealthCheck(
        "embeddings",
        "Semantic recall",
        "warning",
        "No provider offers embeddings (OpenAI or Gemini key): memory and knowledge recall "
        "match keywords only.",
        "Add an OpenAI or Gemini key",
        "/settings/org/models",
    )


def _search_check() -> HealthCheck:
    name = configured_provider_name()
    if name != "duckduckgo":
        return HealthCheck("search", "Web search", "ok", f"Web search through {name}.")
    return HealthCheck(
        "search",
        "Web search",
        "warning",
        "Web search scrapes DuckDuckGo, which breaks without notice. Configure SearXNG, Brave "
        "or Tavily (ROCKET_SEARCH_PROVIDER).",
        "Search providers",
        f"{DOCS}/install/configuration",
    )


async def _github_check(db_mgr: Any, org_id: str) -> HealthCheck:
    installations = await installations_for_org(db_mgr, org_id)
    if installations:
        names = ", ".join(sorted(i.account_login for i in installations))
        return HealthCheck("github", "GitHub App", "ok", f"Installed on {names}.")
    return HealthCheck(
        "github",
        "GitHub App",
        "warning",
        "The GitHub App is not installed: the agent cannot clone private repositories or open "
        "pull requests as the bot.",
        "Install the GitHub App",
        "/settings/org/git",
    )


async def _images_check(db_mgr: Any, org_id: str, driver: Any) -> HealthCheck:
    probe = getattr(driver, "image_available", None)
    if probe is None:
        return HealthCheck(
            "sandbox_images",
            "Sandbox images",
            "unknown",
            "This sandbox driver pulls images on each node when a session starts.",
        )
    entries = await SandboxCatalogue(db_mgr).entries(org_id)
    missing = [e.image for e in entries if not await probe(e.image)]
    if not missing:
        return HealthCheck(
            "sandbox_images", "Sandbox images", "ok", "Every catalogue image is on the host."
        )
    default = next((e.image for e in entries if e.is_default), "")
    status: Status = "error" if default in missing else "warning"
    return HealthCheck(
        "sandbox_images",
        "Sandbox images",
        status,
        f"Not pulled yet (the first session on each waits for it): {', '.join(missing)}.",
        "Sandbox types",
        "/settings/org/sandboxes",
    )


async def _guarded(check_id: str, check: Awaitable[HealthCheck]) -> HealthCheck:
    try:
        return await check
    except Exception:
        logger.warning("Setup health check %s failed", check_id, exc_info=True)
        title = check_id.replace("_", " ").title()
        return HealthCheck(check_id, title, "unknown", "Could not check.")


async def setup_health(
    db_mgr: Any, org_id: str, driver: Any, deployment_default: str
) -> list[HealthCheck]:
    """Every check; one that cannot run is reported as unknown, never fails the card."""
    return [
        await _guarded("models", _model_check(db_mgr, org_id, deployment_default)),
        await _guarded("embeddings", _embedding_check(db_mgr, org_id)),
        _search_check(),
        await _guarded("github", _github_check(db_mgr, org_id)),
        await _guarded("sandbox_images", _images_check(db_mgr, org_id, driver)),
    ]
