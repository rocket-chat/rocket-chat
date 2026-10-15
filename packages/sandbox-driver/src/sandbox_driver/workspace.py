"""Which folder of the sandbox a git, build or test tool acts on.

``clone_repository`` puts each repository in ``/workspace/<name>``, so ``/workspace`` itself is
usually not a repository. Every tool that runs ``git`` or a project command resolves its folder
here, in one order: the folder the agent named, the session's active repository, ``/workspace``
when it is a repository itself, the only repository under it. Several candidates and no choice
is an error that names them, never a guess.
"""

from __future__ import annotations

import posixpath
import shlex

from sandbox_driver.exceptions import SandboxError
from specifications.interfaces.sandbox import SandboxDriverProtocol

WORKSPACE = "/workspace"
REPOSITORY_SEARCH_DEPTH = 3
_PROBE_TIMEOUT_SECONDS = 30

# One probe for the automatic case: /workspace itself, else every repository under it.
# `-prune` stops at a repository's own `.git`, so nested dependencies are not listed.
_FIND_REPOSITORIES = (
    "git rev-parse --show-toplevel 2>/dev/null && exit 0; "
    f"find . -maxdepth {REPOSITORY_SEARCH_DEPTH} -name .git -not -path '*/node_modules/*' "
    "-prune -print 2>/dev/null | sed 's|^\\./||; s|/\\.git$||' | sort | sed 's|^|@|'"
)


class RepositoryNotFoundError(SandboxError):
    """No repository (or more than one, with no choice made) could be resolved."""


def workspace_path(path: str) -> str:
    """``path`` as an absolute, normalised path; relative paths are under ``/workspace``."""
    cleaned = path.strip()
    absolute = cleaned if cleaned.startswith("/") else posixpath.join(WORKSPACE, cleaned)
    return posixpath.normpath(absolute)


async def _is_repository(driver: SandboxDriverProtocol, session_id: str, folder: str) -> bool:
    result = await driver.exec_command(
        session_id,
        f"git -C {shlex.quote(folder)} rev-parse --git-dir",
        workdir=WORKSPACE,
        timeout_seconds=_PROBE_TIMEOUT_SECONDS,
    )
    return result.exit_code == 0


async def list_repositories(driver: SandboxDriverProtocol, session_id: str) -> list[str]:
    """``/workspace`` when it is a repository, else every repository under it (sorted)."""
    result = await driver.exec_command(
        session_id, _FIND_REPOSITORIES, workdir=WORKSPACE, timeout_seconds=_PROBE_TIMEOUT_SECONDS
    )
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if lines and not lines[0].startswith("@"):
        return [lines[0]]
    return [workspace_path(line[1:]) for line in lines if line.startswith("@") and line[1:]]


async def resolve_repository_dir(
    driver: SandboxDriverProtocol,
    session_id: str,
    requested: str | None = None,
    active: str | None = None,
) -> str:
    """The folder of the git repository a tool should act on (see the module docstring)."""
    if requested and requested.strip():
        folder = workspace_path(requested)
        if not await _is_repository(driver, session_id, folder):
            raise RepositoryNotFoundError(f"{folder} is not a git repository.")
        return folder
    if active and await _is_repository(driver, session_id, workspace_path(active)):
        return workspace_path(active)
    return _only(await list_repositories(driver, session_id))


def _only(found: list[str]) -> str:
    if len(found) == 1:
        return found[0]
    if not found:
        raise RepositoryNotFoundError(
            f"There is no git repository under {WORKSPACE}: clone one first with clone_repository."
        )
    raise RepositoryNotFoundError(
        f"There are several repositories under {WORKSPACE} ({', '.join(found)}): say which one "
        "with the path argument."
    )


async def resolve_project_dir(
    driver: SandboxDriverProtocol,
    session_id: str,
    requested: str | None = None,
    active: str | None = None,
) -> str:
    """The folder a project command (tests, lint, build, install) runs in.

    Unlike :func:`resolve_repository_dir` it needs no git repository: a named folder is used as
    given, and a workspace without any repository runs in ``/workspace``. Several repositories and
    no choice is still an error, because running in ``/workspace`` would test none of them.
    """
    if requested and requested.strip():
        return workspace_path(requested)
    if active and await _is_repository(driver, session_id, workspace_path(active)):
        return workspace_path(active)
    found = await list_repositories(driver, session_id)
    return _only(found) if found else WORKSPACE
