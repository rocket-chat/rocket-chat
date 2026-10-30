"""Structured git mutation tools with checkpoint/undo support.

Interactive git operations shell out to the native ``git`` CLI inside the sandbox
(the industry-standard approach for agent harnesses, e.g. Claude Code / Codex),
while commit signing, co-authorship trailers and PR creation delegate to the
bound :class:`GitEngine` when one is available.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from agent_core.tools.clamp import clamp_output
from agent_core.tools.tier2 import _sh_quote
from specifications.interfaces.agent import GitPolicy
from specifications.interfaces.git import (
    CoAuthor,
    CommitSpec,
    PublishResult,
    PublishSpec,
    SigningMode,
)
from specifications.interfaces.sandbox import ExecResult, SandboxDriverProtocol

CHECKPOINT_TAG_PREFIX = "rocket-checkpoint-"
# Where the session's own work starts in a repository: what was cloned (or, for a workspace that
# was not cloned by the platform, the first snapshot before a change). The Changes view measures
# from it, so a session that checks out a long-lived branch shows what *it* changed, not the
# branch's whole history against the default branch.
SESSION_BASE_REF = "refs/rocket/session-base"
# ``GitPolicy.signing`` as the publishing engine's modes.
ENGINE_SIGNING: dict[str, SigningMode] = {
    "github": SigningMode.GITHUB_APP,
    "member_gpg": SigningMode.GPG_KEY,
    "none": SigningMode.NONE,
}


def mark_session_base_command(revision: str = "HEAD") -> str:
    """Shell that records ``revision`` (already quoted) as the session's base, once."""
    return (
        f"git rev-parse -q --verify {SESSION_BASE_REF} >/dev/null 2>&1 "
        f"|| git update-ref {SESSION_BASE_REF} {revision} 2>/dev/null"
    )


async def mark_session_base_from_snapshot(
    driver: SandboxDriverProtocol, session_id: str, workdir: str
) -> None:
    """Record the newest checkpoint as the session's base when the repository has none yet."""
    newest = (
        "\"$(git for-each-ref --sort=-creatordate --count=1 --format='%(objectname)' "
        f'{_sh_quote("refs/tags/" + CHECKPOINT_TAG_PREFIX + "*")})"'
    )
    await _exec(driver, session_id, f"{mark_session_base_command(newest)}; true", workdir)


class CommitCapable(Protocol):
    """Minimal GitEngine surface required by the git tools."""

    async def create_commit(self, session_id: str, spec: CommitSpec) -> str: ...

    async def publish_branch(self, session_id: str, spec: PublishSpec) -> PublishResult: ...

    async def push_and_open_pr(
        self,
        session_id: str,
        title: str,
        body: str,
        target_branch: str | None = None,
        token: str | None = None,
        installations: Sequence[tuple[int, str]] = (),
        publish: PublishSpec | None = None,
    ) -> str: ...


async def _exec(
    driver: SandboxDriverProtocol, session_id: str, command: str, workdir: str = "/workspace"
) -> ExecResult:
    return await driver.exec_command(
        session_id=session_id, command=command, workdir=workdir, timeout_seconds=60
    )


def _format_result(result: ExecResult, success_message: str) -> str:
    if result.exit_code != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        return f"Error: {detail}"
    output = (result.stdout or "").strip()
    return f"{success_message}\n{clamp_output(output)}".strip()


def commit_spec_for_policy(
    message: str,
    branch: str | None,
    extra_co_authors: list[str] | None,
    policy: GitPolicy,
) -> CommitSpec:
    """Build the commit from the organization's git policy.

    The member commits under their own name when they sign with their key (``member_gpg``);
    otherwise the bot commits and the member is credited with a ``Co-authored-by`` trailer.
    """
    as_member = policy.commits_as_member
    co_authors = [CoAuthor(name=a, email="") for a in (extra_co_authors or [])]
    if not as_member:
        co_authors.extend(_credited(policy))
    return CommitSpec(
        message=message,
        branch=branch or "",
        co_authors=co_authors,
        signing_mode=ENGINE_SIGNING.get(policy.signing, SigningMode.GITHUB_APP),
        gpg_key_id=policy.signing_key_id or None,
        committer_name=policy.user_name if as_member else policy.bot_name,
        committer_email=policy.user_email if as_member else policy.bot_email,
    )


def publish_spec_for_policy(
    policy: GitPolicy,
    branch: str | None = None,
    force_with_lease: bool = False,
    path: str | None = None,
) -> PublishSpec:
    """How the branch leaves the sandbox under the member's effective policy.

    GitHub-signed commits (``github``) are re-created by the App, crediting the member. Locally
    made commits (``member_gpg``, ``none``) are pushed with the member's token, else the App's.
    """
    signing = ENGINE_SIGNING.get(policy.signing, SigningMode.NONE)
    signed_by_github = signing == SigningMode.GITHUB_APP
    return PublishSpec(
        branch=branch or "",
        signing_mode=signing,
        token=None if signed_by_github else policy.user_token,
        fallback_token=policy.user_token,
        path=path or "",
        installations=policy.app_installations,
        default_branch=policy.default_branch,
        force_with_lease=force_with_lease,
        signing_key=policy.signing_key if signing == SigningMode.GPG_KEY else None,
        co_authors=_credited(policy) if signed_by_github else (),
    )


def _credited(policy: GitPolicy) -> tuple[CoAuthor, ...]:
    """The member, credited on the commits the bot makes."""
    if policy.user_name and policy.user_email:
        return (CoAuthor(name=policy.user_name, email=policy.user_email),)
    return ()


async def git_commit(
    driver: SandboxDriverProtocol,
    session_id: str,
    message: str,
    branch: str | None = None,
    co_authors: list[str] | None = None,
    git_engine: CommitCapable | None = None,
    policy: GitPolicy | None = None,
    workdir: str = "/workspace",
) -> str:
    """Create a commit that follows the organization's authorship and signing policy."""
    spec = commit_spec_for_policy(message, branch, co_authors, policy or GitPolicy())
    spec.path = workdir
    if git_engine is not None:
        try:
            sha = await git_engine.create_commit(session_id, spec)
            return f"Committed via GitEngine ({spec.signing_mode.value} signing). SHA: {sha}"
        except Exception as err:
            return f"Error: git commit failed via GitEngine: {err}"

    if branch:
        await _exec(driver, session_id, f"git checkout -B {_sh_quote(branch)}", workdir)
    await _exec(driver, session_id, "git add -A", workdir)
    result = await _exec(driver, session_id, f"git commit -m {_sh_quote(message)}", workdir)
    combined = f"{result.stdout or ''}{result.stderr or ''}"
    if result.exit_code != 0 and "nothing to commit" in combined:
        rev = await _exec(driver, session_id, "git rev-parse HEAD", workdir)
        return f"Nothing to commit. HEAD: {(rev.stdout or '').strip()}"
    rev = await _exec(driver, session_id, "git rev-parse HEAD", workdir)
    return _format_result(result, f"Committed. SHA: {(rev.stdout or '').strip()}")


async def git_branch(
    driver: SandboxDriverProtocol,
    session_id: str,
    name: str,
    from_ref: str | None = None,
    workdir: str = "/workspace",
) -> str:
    """Create and switch to a new branch, optionally from a specific ref."""
    base = f" {_sh_quote(from_ref)}" if from_ref else ""
    result = await _exec(driver, session_id, f"git checkout -b {_sh_quote(name)}{base}", workdir)
    return _format_result(result, f"Switched to new branch '{name}'.")


MAX_CHECKPOINTS = 50

# Snapshot the *whole* working tree (tracked, modified and new files, honouring .gitignore)
# into a commit without touching the real index, branch or worktree, then tag that commit.
# Tagging HEAD alone would silently omit the very edits a user wants to be able to rewind.
_SNAPSHOT_SCRIPT = """
set -e
index=$(mktemp)
trap 'rm -f "$index"' EXIT
export GIT_INDEX_FILE="$index"
parent=$(git rev-parse -q --verify HEAD || true)
if [ -n "$parent" ]; then git read-tree HEAD; fi
git add -A
tree=$(git write-tree)
identity="-c user.name=rocket-chat -c user.email=checkpoint@rocket.chat"
if [ -n "$parent" ]; then
  commit=$(git $identity commit-tree "$tree" -p "$parent" -m {message})
else
  commit=$(git $identity commit-tree "$tree" -m {message})
fi
git tag -f {tag} "$commit" >/dev/null
git for-each-ref --sort=-creatordate --format='%(refname)' 'refs/tags/{prefix}*' \
  | tail -n +{keep} | while read -r ref; do git update-ref -d "$ref"; done
echo "$commit"
"""


@dataclass(frozen=True)
class Checkpoint:
    name: str
    sha: str
    created_at: int


async def git_checkpoint(
    driver: SandboxDriverProtocol,
    session_id: str,
    label: str | None = None,
    workdir: str = "/workspace",
) -> str:
    """Snapshot the workspace (including uncommitted work) as a restorable checkpoint."""
    listed = await _exec(
        driver, session_id, f"git tag -l {_sh_quote(CHECKPOINT_TAG_PREFIX + '*')} | wc -l", workdir
    )
    try:
        next_index = int((listed.stdout or "0").strip()) + 1
    except ValueError:
        next_index = 1
    suffix = f"-{_safe_label(label)}" if label else ""
    tag = f"{CHECKPOINT_TAG_PREFIX}{next_index}{suffix}"
    result = await _exec(
        driver,
        session_id,
        _SNAPSHOT_SCRIPT.format(
            message=_sh_quote(f"Rocket Chat checkpoint {tag}"),
            tag=_sh_quote(tag),
            prefix=CHECKPOINT_TAG_PREFIX,
            keep=MAX_CHECKPOINTS + 1,
        ),
        workdir,
    )
    sha = (result.stdout or "").strip().splitlines()[-1] if result.exit_code == 0 else ""
    return _format_result(result, f"Checkpoint '{tag}' created at {sha[:12]}.")


def _safe_label(label: str) -> str:
    """A label that is a valid ref component: letters, digits, dash, underscore, dot."""
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in label.strip())
    return cleaned.strip("-.")[:40]


async def list_checkpoints(
    driver: SandboxDriverProtocol, session_id: str, workdir: str = "/workspace"
) -> list[Checkpoint]:
    """Checkpoints of the repository in ``workdir``, newest first (empty outside a repository)."""
    result = await _exec(
        driver,
        session_id,
        "git for-each-ref --sort=-creatordate "
        "--format='%(refname:strip=2)\t%(objectname:short)\t%(creatordate:unix)' "
        f"{_sh_quote('refs/tags/' + CHECKPOINT_TAG_PREFIX + '*')}",
        workdir,
    )
    if result.exit_code != 0:
        return []
    checkpoints: list[Checkpoint] = []
    for line in (result.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[2].isdigit():
            checkpoints.append(Checkpoint(parts[0], parts[1], int(parts[2])))
    return checkpoints


async def is_git_workspace(
    driver: SandboxDriverProtocol, session_id: str, workdir: str = "/workspace"
) -> bool:
    return (await _exec(driver, session_id, "git rev-parse --git-dir", workdir)).exit_code == 0


async def git_revert(
    driver: SandboxDriverProtocol,
    session_id: str,
    reference: str,
    mode: str = "files",
    workdir: str = "/workspace",
) -> str:
    """Restore the workspace to a checkpoint/commit.

    ``mode='files'`` restores tracked files only (safe). ``mode='hard'`` resets
    the whole worktree and MUST only be reached after human approval.
    """
    if mode == "hard":
        result = await _exec(
            driver, session_id, f"git reset --hard {_sh_quote(reference)}", workdir
        )
        return _format_result(result, f"Hard reset to {reference}.")
    result = await _exec(driver, session_id, f"git checkout {_sh_quote(reference)} -- .", workdir)
    return _format_result(result, f"Restored tracked files from {reference}.")


async def git_push(
    session_id: str,
    branch: str | None = None,
    force_with_lease: bool = False,
    git_engine: CommitCapable | None = None,
    policy: GitPolicy | None = None,
    path: str | None = None,
    published: Callable[[str, str, str, str], Awaitable[None]] | None = None,
) -> str:
    """Publish the branch: the one way commits leave the sandbox (see ``BranchPublisher``)."""
    if git_engine is None:
        return "Error: GitHub is not configured; cannot publish the branch."
    spec = publish_spec_for_policy(policy or GitPolicy(), branch, force_with_lease, path)
    try:
        result = await git_engine.publish_branch(session_id, spec)
    except Exception as err:
        return f"Error: could not publish the branch: {err}"
    if published is not None and result.head_sha:
        await published(session_id, result.repository, result.branch, result.head_sha)
    return describe_publication(result)


def describe_publication(result: PublishResult) -> str:
    if result.via == "github_api":
        how = "created through GitHub's API, so GitHub signed them"
        if result.commits == 0:
            return (
                f"Nothing to publish: {result.branch} is already up to date on {result.repository}."
            )
        lines = [
            f"Published {result.commits} commit(s) to {result.repository}@{result.branch} ({how})."
            f" Head: {result.head_sha[:12]}."
        ]
    else:
        lines = [f"Pushed {result.repository}@{result.branch}. Head: {result.head_sha[:12]}."]
    lines.extend(result.notices)
    if result.rewritten:
        # The agent must not keep using hashes that no longer exist (in a message, a revert,
        # a later rebase): it is told which commit became which.
        lines.append(
            f"The commits were re-created on publication, so their hashes changed; your local "
            f"branch {result.branch} now points at the published history:"
        )
        lines.extend(f"  {old[:12]} -> {new[:12]}" for old, new in result.rewritten)
    return "\n".join(lines)


async def git_open_pr(
    driver: SandboxDriverProtocol,
    session_id: str,
    title: str,
    body: str,
    target_branch: str | None = None,
    git_engine: CommitCapable | None = None,
    policy: GitPolicy | None = None,
    path: str | None = None,
) -> str:
    """Push the working branch and open a pull request per the organization's PR policy."""
    if git_engine is None:
        return "Error: GitHub App credentials are not configured; cannot open a pull request."
    effective = policy or GitPolicy()
    token = effective.user_token if effective.publishes_as_member else None
    try:
        url = await git_engine.push_and_open_pr(
            session_id,
            title,
            body,
            target_branch,
            token=token,
            installations=effective.app_installations,
            publish=publish_spec_for_policy(effective, path=path),
        )
        return f"Pull request opened: {url}"
    except Exception as err:
        return f"Error: failed to open pull request: {err}"


_PULL_REQUEST_URL = re.compile(r"https://[^/\s]+/([\w.-]+/[\w.-]+)/pull/(\d+)")


def parse_pull_request_url(text: str) -> tuple[str, int] | None:
    """``(owner/name, number)`` of the pull request a ``git_open_pr`` result links to."""
    if not text.startswith("Pull request opened") or not (match := _PULL_REQUEST_URL.search(text)):
        return None
    return match.group(1), int(match.group(2))


_RISKY_GIT_OPERATIONS = (
    ("rebase", r"\bgit\s+(?:-[^\s]+\s+)*rebase\b"),
    ("reset_hard", r"\bgit\s+(?:-[^\s]+\s+)*reset\b.*--(?:hard|merge)\b"),
    ("merge", r"\bgit\s+(?:-[^\s]+\s+)*merge\b"),
    ("cherry_pick", r"\bgit\s+(?:-[^\s]+\s+)*cherry-pick\b"),
    ("filter_branch", r"\bgit\s+(?:-[^\s]+\s+)*filter-branch\b"),
    ("force_push", r"\bgit\s+(?:-[^\s]+\s+)*push\s+.*--(?:force|force-with-lease)\b"),
    ("force_push", r"\bgit\s+(?:-[^\s]+\s+)*push\s+.*-[a-zA-Z]*f\b"),
)


def classify_risky_git_operation(command: str) -> str | None:
    """Return the operation name if the command rewrites history or discards changes, else None."""
    cmd = command.strip().lower()
    if "--abort" in cmd:
        return None
    for name, pattern in _RISKY_GIT_OPERATIONS:
        if re.search(pattern, cmd):
            return name
    return None


_MANUAL_GITHUB_WRITE_NOTICES = (
    (
        r"\bgit\s+(?:-[^\s]+\s+)*push\b",
        "Direct 'git push' from the sandbox shell cannot authenticate because the sandbox holds no credentials. "
        "Use the native 'git_push' tool (or 'git_open_pr') instead. It pushes your branch through Rocket Chat's "
        "authenticated GitEngine, applies GitHub App tokens, and signs commits automatically.",
    ),
    (
        r"\bgh\s+pr\s+(?:create|merge|close|edit|reopen|review)\b",
        "The 'gh' CLI is not authenticated in the sandbox. Use the native 'git_open_pr' tool to open pull requests, "
        "or 'github_pull_request' for reviews and checks.",
    ),
    (
        r"\bgh\s+issue\s+(?:create|close|edit|reopen|comment|delete)\b",
        "The 'gh' CLI is not authenticated in the sandbox. Use the native 'github_issues' tool instead.",
    ),
    (
        r"\bgh\s+release\s+(?:create|delete|edit|upload)\b",
        "The 'gh' CLI is not authenticated in the sandbox. Use the native 'github_releases' tool instead.",
    ),
)


def notice_for_manual_github_write(command: str) -> str | None:
    """Return an instructional notice if the command attempts git push or gh CLI write operations."""
    cmd = command.strip().lower()
    for pattern, guidance in _MANUAL_GITHUB_WRITE_NOTICES:
        if re.search(pattern, cmd):
            return f"[Platform Guidance: {guidance}]"
    return None
