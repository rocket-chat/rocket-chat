"""Workspace seeding for session forks.

A fork must never mutate the parent's workspace, so the child always receives its own
storage. The drivers expose no cross-volume primitive, so seeding is an eager copy of the
parent's files — including ``.git`` when the parent is a repository — into a freshly
created child volume. That preserves the copy-on-write *semantic* (the parent is
untouched, the child diverges independently); a future driver primitive can replace the
copy with a ``git clone --shared``/reflink without changing this contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from specifications.interfaces.sandbox import SandboxDriverProtocol, WorkspaceSpec

# A fork copies the whole tree, so the listing depth must not silently truncate a deep
# repository into a half-seeded workspace.
MAX_SEED_DEPTH = 128

# Origin markers recorded in ``metadata.workspace_origin`` so the API (and the driver
# policy) can tell how the child workspace was produced.
ORIGIN_GIT = "git"
ORIGIN_FILES = "files"
ORIGIN_EMPTY = "empty"


@dataclass(frozen=True)
class SeedResult:
    """How a child workspace was seeded, and how many files it received."""

    origin: str
    file_count: int


def _relative_path(path: str, workspace_prefix: str) -> str:
    """Reduce an absolute workspace path to its prefix-relative form.

    Drivers disagree on the shape of ``list_files`` results (the fake driver returns bare
    relative keys, Docker returns ``/workspace/...``), so both are normalised here before
    the git marker is detected.
    """
    trimmed = path.strip().strip("/")
    prefix = workspace_prefix.strip("/")
    if prefix and (trimmed == prefix or trimmed.startswith(f"{prefix}/")):
        trimmed = trimmed[len(prefix) :].strip("/")
    return trimmed


def _looks_like_git(relative_paths: list[str]) -> bool:
    """A workspace is a repository when it carries git metadata at its root."""
    return any(path == ".git" or path.startswith(".git/") for path in relative_paths)


async def seed_workspace_from(
    driver: SandboxDriverProtocol,
    parent_session_id: str,
    child_session_id: str,
    child_spec: WorkspaceSpec,
    *,
    workspace_prefix: str = "/workspace",
) -> SeedResult:
    """Create the child workspace and copy the parent's files into it.

    Returns the origin marker for ``metadata.workspace_origin``: ``git`` when the parent
    is a repository (its ``.git`` metadata is copied, so history travels with the fork),
    ``files`` for a plain directory, and ``empty`` when the parent had no files.
    """
    await driver.ensure_workspace(child_spec)

    parent_files = await driver.list_files(
        parent_session_id, directory=workspace_prefix, max_depth=MAX_SEED_DEPTH
    )
    relative_paths = [_relative_path(stat.path, workspace_prefix) for stat in parent_files]
    is_git = _looks_like_git(relative_paths)

    copied = 0
    for stat in parent_files:
        if stat.is_dir:
            continue
        content = await driver.read_file(parent_session_id, stat.path)
        await driver.write_file(child_session_id, stat.path, content, overwrite=True)
        copied += 1

    if is_git:
        return SeedResult(origin=ORIGIN_GIT, file_count=copied)
    if copied:
        return SeedResult(origin=ORIGIN_FILES, file_count=copied)
    return SeedResult(origin=ORIGIN_EMPTY, file_count=0)
