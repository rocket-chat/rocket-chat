"""What changed in a session's repositories: the cockpit's Changes view.

A change is measured against a base, not replayed from events: the diffs the agent's tools
report describe one edit each (the last one, for a file edited twice), while the person
reviewing wants the file as it stands against where it started. The base is either the snapshot
the platform takes before a turn first changes the workspace (``turn``) or the point the work
branched from the repository's default branch (``session``). Everything is read with git in the
sandbox, so it is correct after a reload, a restart or a second tab.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Literal

from agent_core.tools import git_tools
from sandbox_driver.workspace import list_repositories

from specifications.interfaces.sandbox import SandboxDriverProtocol

Scope = Literal["session", "turn"]
SCOPES: tuple[str, ...] = ("session", "turn")
Status = Literal["added", "modified", "deleted"]

MAX_FILES = 200
MAX_FILE_BYTES = 400_000
_GIT_TIMEOUT_SECONDS = 30
_TURN_CHECKPOINT_SUFFIX = "-turn"


class ChangesError(ValueError):
    """The request names something that is not a repository or file of the session."""


@dataclass(frozen=True)
class ChangedFile:
    path: str
    status: Status
    additions: int
    deletions: int
    binary: bool = False


@dataclass(frozen=True)
class RepositoryChanges:
    path: str
    name: str
    base_label: str
    files: list[ChangedFile] = field(default_factory=list)
    truncated: bool = False


@dataclass(frozen=True)
class FileVersions:
    path: str
    original: str | None
    modified: str | None
    binary: bool = False
    truncated: bool = False


async def _git(
    driver: SandboxDriverProtocol, session_id: str, repository: str, command: str
) -> tuple[int, str]:
    result = await driver.exec_command(
        session_id, command, workdir=repository, timeout_seconds=_GIT_TIMEOUT_SECONDS
    )
    return result.exit_code, result.stdout or ""


async def _base(
    driver: SandboxDriverProtocol, session_id: str, repository: str, scope: str
) -> tuple[str, str]:
    """``(revision, label)`` the working tree is compared with."""
    if scope == "turn":
        for checkpoint in await git_tools.list_checkpoints(driver, session_id, repository):
            if checkpoint.name.endswith(_TURN_CHECKPOINT_SUFFIX):
                return checkpoint.name, "since the start of the latest turn"
        return "HEAD", "since the last commit"
    code, head = await _git(
        driver, session_id, repository, "git symbolic-ref --short refs/remotes/origin/HEAD"
    )
    default = head.strip()
    if code == 0 and default:
        code, merge_base = await _git(
            driver, session_id, repository, f"git merge-base HEAD {shlex.quote(default)}"
        )
        if code == 0 and merge_base.strip():
            return merge_base.strip(), f"since it branched from {default.removeprefix('origin/')}"
    return "HEAD", "since the last commit"


def _split(output: str) -> list[str]:
    return [item for item in output.split("\0") if item]


def _count(value: str) -> int:
    return int(value) if value.isdigit() else 0


def _statuses(output: str) -> dict[str, Status]:
    """``git diff --name-status -z`` as path -> status."""
    items = _split(output)
    statuses: dict[str, Status] = {}
    for code, path in zip(items[::2], items[1::2], strict=False):
        statuses[path] = "added" if code == "A" else "deleted" if code == "D" else "modified"
    return statuses


def _numbers(output: str) -> dict[str, tuple[int, int, bool]]:
    """``git diff --numstat -z`` as path -> (additions, deletions, binary)."""
    numbers: dict[str, tuple[int, int, bool]] = {}
    for item in _split(output):
        added, _, rest = item.partition("\t")
        deleted, _, path = rest.partition("\t")
        numbers[path] = (_count(added), _count(deleted), added == "-")
    return numbers


async def repository_changes(
    driver: SandboxDriverProtocol, session_id: str, repository: str, scope: str
) -> RepositoryChanges:
    """Every file of ``repository`` that differs from the base of ``scope``."""
    base, label = await _base(driver, session_id, repository, scope)
    quoted = shlex.quote(base)
    _, names = await _git(
        driver, session_id, repository, f"git diff --name-status -z --no-renames {quoted}"
    )
    _, counts = await _git(
        driver, session_id, repository, f"git diff --numstat -z --no-renames {quoted}"
    )
    _, untracked = await _git(
        driver, session_id, repository, "git ls-files --others --exclude-standard -z"
    )
    statuses, numbers = _statuses(names), _numbers(counts)
    files = []
    for path, status in statuses.items():
        added, deleted, binary = numbers.get(path, (0, 0, False))
        files.append(ChangedFile(path, status, added, deleted, binary))
    for path in _split(untracked):
        lines = await _line_count(driver, session_id, repository, path)
        files.append(ChangedFile(path, "added", lines, 0))
        if len(files) > MAX_FILES:
            break
    files.sort(key=lambda changed: changed.path)
    return RepositoryChanges(
        path=repository,
        name=repository.rstrip("/").rsplit("/", 1)[-1],
        base_label=label,
        files=files[:MAX_FILES],
        truncated=len(files) > MAX_FILES,
    )


async def _line_count(
    driver: SandboxDriverProtocol, session_id: str, repository: str, path: str
) -> int:
    code, out = await _git(driver, session_id, repository, f"wc -l < {shlex.quote(path)}")
    return _count(out.strip()) if code == 0 else 0


async def session_changes(
    driver: SandboxDriverProtocol, session_id: str, scope: str
) -> list[RepositoryChanges]:
    """The changes of every repository in the workspace."""
    return [
        await repository_changes(driver, session_id, repository, scope)
        for repository in await list_repositories(driver, session_id)
    ]


def _checked_path(path: str) -> str:
    parts = path.split("/")
    if not path or path.startswith("/") or ".." in parts or "\0" in path or "\n" in path:
        raise ChangesError(f"'{path}' is not a path inside the repository")
    return path


async def _checked_repository(
    driver: SandboxDriverProtocol, session_id: str, repository: str
) -> str:
    if repository not in await list_repositories(driver, session_id):
        raise ChangesError(f"'{repository}' is not a repository of this session")
    return repository


async def _is_binary(
    driver: SandboxDriverProtocol, session_id: str, repository: str, source: str
) -> bool:
    """Whether the first bytes of ``source`` (a shell command printing the file) hold a NUL."""
    code, out = await _git(
        driver,
        session_id,
        repository,
        f"{source} | head -c 8000 | tr -d '\\000' | wc -c; {source} | head -c 8000 | wc -c",
    )
    sizes = out.split()
    return code == 0 and len(sizes) == 2 and sizes[0] != sizes[1]


async def file_versions(
    driver: SandboxDriverProtocol, session_id: str, repository: str, scope: str, path: str
) -> FileVersions:
    """The file at the base and as it is now, whole, for a side-by-side view."""
    await _checked_repository(driver, session_id, repository)
    path = _checked_path(path)
    base, _ = await _base(driver, session_id, repository, scope)
    in_base = f"git cat-file -e {shlex.quote(f'{base}:{path}')} 2>/dev/null"
    original_source = f"git show {shlex.quote(f'{base}:{path}')}"
    modified_source = f"cat -- {shlex.quote(path)}"
    original = await _read(driver, session_id, repository, in_base, original_source)
    modified = await _read(
        driver, session_id, repository, f"[ -f {shlex.quote(path)} ]", modified_source
    )
    if original is None and modified is None:
        return FileVersions(path, None, None)
    binary = False
    for text, source in ((original, original_source), (modified, modified_source)):
        if text is not None and await _is_binary(driver, session_id, repository, source):
            binary = True
    if binary:
        return FileVersions(path, None, None, binary=True)
    truncated = any(len(text or "") > MAX_FILE_BYTES for text in (original, modified))
    return FileVersions(
        path,
        None if original is None else original[:MAX_FILE_BYTES],
        None if modified is None else modified[:MAX_FILE_BYTES],
        truncated=truncated,
    )


async def _read(
    driver: SandboxDriverProtocol, session_id: str, repository: str, exists: str, source: str
) -> str | None:
    """The output of ``source`` (up to the size limit), or None when ``exists`` fails."""
    code, out = await _git(
        driver, session_id, repository, f"{exists} && {source} | head -c {MAX_FILE_BYTES + 1}"
    )
    return out if code == 0 else None


async def revert_file(
    driver: SandboxDriverProtocol, session_id: str, repository: str, scope: str, path: str
) -> None:
    """Put one file back as it was at the base: restored, or removed when it did not exist."""
    await _checked_repository(driver, session_id, repository)
    path = _checked_path(path)
    base, _ = await _base(driver, session_id, repository, scope)
    quoted = shlex.quote(path)
    # A file the base does not have was created since: undoing it removes it. Never fall back to
    # deleting a file the base does have when the restore itself fails.
    code, _ = await _git(
        driver,
        session_id,
        repository,
        f"if git cat-file -e {shlex.quote(f'{base}:{path}')} 2>/dev/null; then "
        f"git restore --source={shlex.quote(base)} --worktree -- {quoted}; "
        f"else rm -f -- {quoted}; fi",
    )
    if code != 0:
        raise ChangesError(f"Could not revert '{path}'")
