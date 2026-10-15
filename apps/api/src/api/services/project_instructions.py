"""Repository-level agent instructions (``AGENTS.md`` / ``CLAUDE.md``) for a session's prompt."""

from __future__ import annotations

from agent_core.tools.project_instructions import instruction_paths, read_instructions

from specifications.interfaces.sandbox import SandboxDriverProtocol

WORKSPACE_ROOT = "/workspace"


def candidate_paths(active_repository: str | None) -> list[str]:
    """Where the instructions live: the session's active repository, else the workspace."""
    roots = [active_repository] if active_repository else []
    roots.append(WORKSPACE_ROOT)
    return [path for root in roots for path in instruction_paths(root)]


async def load_project_instructions(
    driver: SandboxDriverProtocol, session_id: str, active_repository: str | None
) -> str | None:
    """Return the prompt section for the first instructions file found, or ``None``."""
    return await read_instructions(driver, session_id, candidate_paths(active_repository))
