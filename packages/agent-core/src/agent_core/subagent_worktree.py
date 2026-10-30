"""Where an isolated child works and how its changes come back: a git worktree on a branch of
its own, or a sandbox of its own whose changes return as that same kind of branch.

Every isolated child ends with a ``subagent/<id>`` branch in the parent's repository and a base
ref ``refs/rocket/subagent-base/<id>``: the diff between the two is exactly what the child did
(the parent's uncommitted and untracked files at the start are in the base, not in the diff).
Merging applies that diff to the parent's working tree, so the merge works whatever the parent
has done since, and nothing is committed on the parent's behalf.
"""

from __future__ import annotations

import posixpath
import shlex
from dataclasses import dataclass

from sandbox_driver.seeding import seed_workspace_from

from specifications.interfaces.sandbox import SandboxDriverProtocol, WorkspaceSpec

WORKTREES_DIR = "/workspace/.rocket/worktrees"
_GIT_IDENTITY = "-c user.name='Rocket subagent' -c user.email=subagent@rocket.invalid"
SETUP_TIMEOUT_SECONDS = 60
DIFF_STAT_CHARS = 4_000
MERGE_DIFF_CHARS = 60_000


class IsolationError(RuntimeError):
    """The child's own tree could not be prepared: it does not run, rather than writing to the
    parent's tree unannounced."""


@dataclass(frozen=True)
class Worktree:
    directory: str
    branch: str
    base_ref: str
    repository: str


def branch_of(subagent_id: str) -> str:
    return f"subagent/{subagent_id}"


def base_ref_of(subagent_id: str) -> str:
    return f"refs/rocket/subagent-base/{subagent_id}"


def _setup_command(repository: str, directory: str, branch: str, base_ref: str) -> str:
    repo, tree = shlex.quote(repository), shlex.quote(directory)
    return " && ".join(
        [
            f"mkdir -p {shlex.quote(WORKTREES_DIR)}",
            f'BASE="$(git -C {repo} stash create 2>/dev/null)"',
            f'BASE="${{BASE:-$(git -C {repo} rev-parse HEAD)}}"',
            f'git -C {repo} worktree add -q -b {shlex.quote(branch)} {tree} "$BASE"',
            # Untracked files go into the base commit, so they are the starting point and never
            # show as the child's changes.
            f"( (git -C {repo} ls-files --others --exclude-standard -z | "
            f"tar -C {repo} --null -T - -cf - | tar -C {tree} -xf -) || true )",
            f"git -C {tree} add -A",
            f"git {_GIT_IDENTITY} -C {tree} commit -q --allow-empty --no-verify -m 'subagent base'",
            f"git -C {tree} update-ref {shlex.quote(base_ref)} HEAD",
        ]
    )


async def setup_worktree(
    driver: SandboxDriverProtocol, session_id: str, repository: str, subagent_id: str
) -> Worktree:
    """A worktree of ``repository`` with its uncommitted and untracked files, on its own branch."""
    worktree = Worktree(
        directory=posixpath.join(WORKTREES_DIR, subagent_id),
        branch=branch_of(subagent_id),
        base_ref=base_ref_of(subagent_id),
        repository=repository,
    )
    command = _setup_command(repository, worktree.directory, worktree.branch, worktree.base_ref)
    result = await driver.exec_command(session_id, command, timeout_seconds=SETUP_TIMEOUT_SECONDS)
    if result.exit_code != 0:
        detail = (result.stderr or result.stdout or "").strip()[-500:]
        raise IsolationError(f"could not create a worktree of {repository}: {detail}")
    return worktree


async def commit_worktree(
    driver: SandboxDriverProtocol, session_id: str, worktree: Worktree, role: str
) -> None:
    tree = shlex.quote(worktree.directory)
    await driver.exec_command(
        session_id,
        f"git -C {tree} add -A && (git {_GIT_IDENTITY} -C {tree} commit -q --no-verify "
        f"-m {shlex.quote(f'{role} subagent changes')} || true)",
        timeout_seconds=SETUP_TIMEOUT_SECONDS,
    )


async def changed_files(
    driver: SandboxDriverProtocol, session_id: str, repository: str, subagent_id: str
) -> tuple[str, list[str]]:
    """The diff stat and the files the child changed, from git (edits made through a shell
    command count as much as the edit tools')."""
    repo = shlex.quote(repository)
    span = f"{shlex.quote(base_ref_of(subagent_id))} {shlex.quote(branch_of(subagent_id))}"
    stat = await driver.exec_command(
        session_id, f"git -C {repo} diff --stat {span}", timeout_seconds=30
    )
    names = await driver.exec_command(
        session_id, f"git -C {repo} diff --name-only {span}", timeout_seconds=30
    )
    files = [line.strip() for line in (names.stdout or "").splitlines() if line.strip()]
    return (stat.stdout or "").strip()[-DIFF_STAT_CHARS:], files


async def uncommitted_files(
    driver: SandboxDriverProtocol, session_id: str, repository: str
) -> list[str]:
    """Files changed in a tree since its last commit (a child that shared the parent's tree)."""
    result = await driver.exec_command(
        session_id,
        f"git -C {shlex.quote(repository)} status --porcelain --untracked-files=all",
        timeout_seconds=30,
    )
    return [line[3:].strip() for line in (result.stdout or "").splitlines() if len(line) > 3]


async def remove_worktree(
    driver: SandboxDriverProtocol, session_id: str, worktree: Worktree
) -> None:
    tree = shlex.quote(worktree.directory)
    await driver.exec_command(
        session_id,
        f"git -C {shlex.quote(worktree.repository)} worktree remove --force {tree} 2>/dev/null "
        f"|| rm -rf {tree}",
        timeout_seconds=SETUP_TIMEOUT_SECONDS,
    )


async def branch_diff(
    driver: SandboxDriverProtocol, session_id: str, repository: str, subagent_id: str
) -> str:
    """The child's whole change as a unified diff, clamped for an approval card."""
    span = f"{shlex.quote(base_ref_of(subagent_id))} {shlex.quote(branch_of(subagent_id))}"
    result = await driver.exec_command(
        session_id, f"git -C {shlex.quote(repository)} diff {span}", timeout_seconds=30
    )
    if result.exit_code != 0:
        return ""
    diff = result.stdout or ""
    if len(diff) > MERGE_DIFF_CHARS:
        return f"{diff[:MERGE_DIFF_CHARS]}\n[... diff cut at {MERGE_DIFF_CHARS} characters]"
    return diff


async def apply_branch(
    driver: SandboxDriverProtocol, session_id: str, repository: str, subagent_id: str
) -> tuple[bool, str]:
    """Apply the child's change to the parent's working tree; (applied, what git said)."""
    repo = shlex.quote(repository)
    patch = shlex.quote(f"/tmp/rocket-{subagent_id}.patch")
    span = f"{shlex.quote(base_ref_of(subagent_id))} {shlex.quote(branch_of(subagent_id))}"
    result = await driver.exec_command(
        session_id,
        f"git -C {repo} diff --binary {span} > {patch} && "
        f"git -C {repo} apply --3way --whitespace=nowarn {patch}; status=$?; rm -f {patch}; "
        "exit $status",
        timeout_seconds=SETUP_TIMEOUT_SECONDS,
    )
    return result.exit_code == 0, (result.stdout + result.stderr).strip()[-1_500:]


# --- a sandbox of its own ---------------------------------------------------------------------


def child_sandbox_id(session_id: str, subagent_id: str) -> str:
    return f"{session_id}--{subagent_id}"[:64]


async def provision_child_sandbox(
    driver: SandboxDriverProtocol, session_id: str, child_id: str, spec: WorkspaceSpec
) -> None:
    """A sandbox seeded from the session's workspace (as a fork seeds its own)."""
    try:
        await seed_workspace_from(driver, session_id, child_id, spec)
        await driver.start_sandbox(child_id)
    except Exception as err:
        raise IsolationError(f"could not start a sandbox of its own: {err}") from err


async def start_child_branch(
    driver: SandboxDriverProtocol, child_id: str, repository: str, subagent_id: str
) -> None:
    """In the child's sandbox: the seeded tree, as is, becomes the base the change is measured from."""
    repo = shlex.quote(repository)
    command = " && ".join(
        [
            f"git -C {repo} checkout -q -b {shlex.quote(branch_of(subagent_id))}",
            f"git -C {repo} add -A",
            f"git {_GIT_IDENTITY} -C {repo} commit -q --allow-empty --no-verify -m 'subagent base'",
            f"git -C {repo} update-ref {shlex.quote(base_ref_of(subagent_id))} HEAD",
        ]
    )
    result = await driver.exec_command(child_id, command, timeout_seconds=SETUP_TIMEOUT_SECONDS)
    if result.exit_code != 0:
        raise IsolationError(
            f"could not prepare the repository in the child's sandbox: {result.stderr}"
        )


async def bring_back_branch(
    driver: SandboxDriverProtocol,
    session_id: str,
    child_id: str,
    repository: str,
    subagent_id: str,
    role: str,
) -> None:
    """Commit the child's work in its sandbox and recreate it as ``subagent/<id>`` in the
    session's repository, from the session's tree as it is now."""
    repo = shlex.quote(repository)
    await driver.exec_command(
        child_id,
        f"git -C {repo} add -A && (git {_GIT_IDENTITY} -C {repo} commit -q --no-verify "
        f"-m {shlex.quote(f'{role} subagent changes')} || true)",
        timeout_seconds=SETUP_TIMEOUT_SECONDS,
    )
    span = f"{shlex.quote(base_ref_of(subagent_id))} HEAD"
    diff = await driver.exec_command(
        child_id, f"git -C {repo} diff --binary {span}", timeout_seconds=SETUP_TIMEOUT_SECONDS
    )
    worktree = await setup_worktree(driver, session_id, repository, subagent_id)
    patch_path = f"/workspace/.rocket/subagent-{subagent_id}.patch"
    await driver.write_file(session_id, patch_path, diff.stdout or "")
    tree = shlex.quote(worktree.directory)
    applied = await driver.exec_command(
        session_id,
        f"(test ! -s {shlex.quote(patch_path)} || git -C {tree} apply --whitespace=nowarn "
        f"{shlex.quote(patch_path)}); status=$?; rm -f {shlex.quote(patch_path)}; exit $status",
        timeout_seconds=SETUP_TIMEOUT_SECONDS,
    )
    if applied.exit_code != 0:
        await remove_worktree(driver, session_id, worktree)
        raise IsolationError(
            f"the child's changes do not apply to the session's tree: {applied.stderr}"
        )
    await commit_worktree(driver, session_id, worktree, role)
    await remove_worktree(driver, session_id, worktree)
