"""Reaching the dedicated MCP runner that hosts command (stdio) servers.

Command servers are arbitrary programs, so they run neither in the API process (next to the
database credentials and the master key) nor in an agent's sandbox (whose image may lack the
tooling, and where the agent could read the server's credentials). They run in the runner pod,
which holds only the secret it shares with the control plane.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

import httpx
from mcp import Client
from mcp_runner.client import ExitReport, runner_stdio_transport
from mcp_runner.protocol import is_valid_env_name

from api.services.mcp_runtime import McpConnector, McpServerTarget

HEALTH_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class RunnerConfig:
    url: str
    token: str


@dataclass(frozen=True)
class RunnerStatus:
    configured: bool
    available: bool
    detail: str


def runner_config() -> RunnerConfig | None:
    """The runner this deployment uses, or ``None`` when command servers are not provided."""
    url, token = os.getenv("MCP_RUNNER_URL", "").strip(), os.getenv("MCP_RUNNER_TOKEN", "").strip()
    return RunnerConfig(url.rstrip("/"), token) if url and token else None


def environment_for(headers: Mapping[str, str]) -> dict[str, str]:
    """The environment of a command server: the stored pairs whose names a shell can export."""
    return {name: value for name, value in headers.items() if is_valid_env_name(name)}


def connect_stdio(
    config: RunnerConfig, org_id: str, report: ExitReport | None = None
) -> McpConnector:
    def connector(target: McpServerTarget) -> AbstractAsyncContextManager[Client]:
        return Client(
            runner_stdio_transport(
                config.url,
                config.token,
                target.url,
                environment_for(target.headers),
                org_id=org_id,
                server_id=target.server_id,
                setup=target.setup or None,
                exit_report=report,
            )
        )

    return connector


async def runner_status() -> RunnerStatus:
    config = runner_config()
    if config is None:
        return RunnerStatus(False, False, "The MCP runner is not deployed for this installation.")
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{config.url}/health")
        response.raise_for_status()
    except httpx.HTTPError as err:
        return RunnerStatus(True, False, f"The MCP runner is not answering: {err}")
    return RunnerStatus(True, True, "The MCP runner is running.")
