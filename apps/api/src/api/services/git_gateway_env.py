"""How a sandbox's git is pointed at the platform's git gateway (``services/git_gateway.py``)."""

from __future__ import annotations

import os
from urllib.parse import urlparse

from git_engine.config import github_web_url

ROUTE_PREFIX = "/v1/git"
CAPABILITY_HEADER = "X-Rocket-Git-Capability"
DEFAULT_GATEWAY_URL = "http://host.docker.internal:8000"
DEFAULT_GITHUB_API_URL = "https://api.github.com"


def github_api_url_of(git_engine: object | None) -> str:
    """The GitHub API URL a git engine is configured for (github.com without one)."""
    settings = getattr(git_engine, "settings", None)
    return str(getattr(settings, "github_api_url", "") or DEFAULT_GITHUB_API_URL)


def gateway_url() -> str:
    """The gateway's base URL as a sandbox reaches it (not as a browser does)."""
    return (os.getenv("ROCKET_GIT_GATEWAY_URL") or DEFAULT_GATEWAY_URL).rstrip("/")


def gateway_endpoint() -> str:
    """``host:port`` of the gateway, for egress policies that must always let it through."""
    parsed = urlparse(gateway_url())
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return f"{parsed.hostname}:{port}"


def sandbox_git_environment(capability: str, api_url: str) -> dict[str, str]:
    """The environment that points a sandbox's git at the gateway for every GitHub URL.

    ``GIT_CONFIG_COUNT``/``GIT_CONFIG_KEY_n``/``GIT_CONFIG_VALUE_n`` (git 2.31+) add configuration
    for the command they are set on, so no secret is written to a file the agent could keep.
    """
    base = f"{gateway_url()}{ROUTE_PREFIX}/"
    host = urlparse(github_web_url(api_url)).hostname or "github.com"
    entries = [
        (f"url.{base}.insteadOf", f"https://{host}/"),
        (f"url.{base}.insteadOf", f"git@{host}:"),
        (f"url.{base}.insteadOf", f"ssh://git@{host}/"),
        (f"http.{base}.extraHeader", f"{CAPABILITY_HEADER}: {capability}"),
    ]
    environment = {"GIT_CONFIG_COUNT": str(len(entries)), "GIT_TERMINAL_PROMPT": "0"}
    for index, (key, value) in enumerate(entries):
        environment[f"GIT_CONFIG_KEY_{index}"] = key
        environment[f"GIT_CONFIG_VALUE_{index}"] = value
    return environment
