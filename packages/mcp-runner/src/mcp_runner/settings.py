"""Runner configuration, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunnerSettings:
    token: str
    cache_dir: Path = Path("/cache")
    work_dir: Path = Path("/tmp/mcp-work")
    max_processes: int = 32
    max_processes_per_org: int = 8
    max_lifetime_seconds: float = 1800.0

    @classmethod
    def from_env(cls) -> RunnerSettings:
        token = os.environ.get("MCP_RUNNER_TOKEN", "")
        if len(token) < 16:
            raise RuntimeError("MCP_RUNNER_TOKEN must be set to a secret of at least 16 characters")
        return cls(
            token=token,
            cache_dir=Path(os.environ.get("MCP_RUNNER_CACHE_DIR", "/cache")),
            work_dir=Path(os.environ.get("MCP_RUNNER_WORK_DIR", "/tmp/mcp-work")),
            max_processes=int(os.environ.get("MCP_RUNNER_MAX_PROCESSES", "32")),
            max_processes_per_org=int(os.environ.get("MCP_RUNNER_MAX_PROCESSES_PER_ORG", "8")),
            max_lifetime_seconds=float(os.environ.get("MCP_RUNNER_MAX_LIFETIME_SECONDS", "1800")),
        )
