"""Repository-level agent instructions (``AGENTS.md`` / ``CLAUDE.md``) for a session's prompt."""

from __future__ import annotations

import posixpath

from agent_core.tools.project_instructions import instruction_paths, read_instructions

from specifications.interfaces.sandbox import SandboxDriverProtocol

WORKSPACE_ROOT = "/workspace"


def candidate_paths(git_repo: str | None) -> list[str]:
    """Where the instructions live: the checkout of the session's repository, else the workspace."""
    roots: list[str] = []
    if git_repo:
        repo_name = posixpath.basename(git_repo.rstrip("/")).removesuffix(".git")
        if repo_name:
            roots.append(posixpath.join(WORKSPACE_ROOT, repo_name))
    roots.append(WORKSPACE_ROOT)
    return [path for root in roots for path in instruction_paths(root)]


async def load_project_instructions(
    driver: SandboxDriverProtocol, session_id: str, git_repo: str | None
) -> str | None:
    """Return the prompt section for the first instructions file found, or ``None``."""
    return await read_instructions(driver, session_id, candidate_paths(git_repo))
