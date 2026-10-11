"""Getting source code into a blank sandbox: validate a repository, install git, clone it.

A cloud sandbox starts empty. The agent works out which repositories a task needs (from the
request, the knowledge bases and the skills it can read) and clones them with the
``clone_repository`` tool, which is built on these helpers; the API's repository-first sessions
use the same ones for a repository chosen up front, so there is one set of rules.
"""

from __future__ import annotations

import logging
import re
import shlex
from dataclasses import dataclass
from urllib.parse import urlparse

from agent_core.tools.clamp import clamp_output
from specifications.interfaces.sandbox import SandboxDriverProtocol

logger = logging.getLogger(__name__)

_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9_.-]{1,100}$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.-]{0,199}$")
_FOLDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
CLONE_TIMEOUT_SECONDS = 900
INSTALL_TIMEOUT_SECONDS = 300
WORKSPACE = "/workspace"

# Minimal images (python:*-slim, node:*-slim) ship without git or CA certificates, and a clone
# over https needs both. Package installation needs root, so a hardened non-root sandbox
# reports the gap instead of failing with "git: not found".
_ENSURE_GIT = (
    "command -v git >/dev/null 2>&1 || ("
    "(apt-get update -qq && apt-get install -y -qq --no-install-recommends git ca-certificates)"
    " || apk add --no-cache git ca-certificates"
    ") >/dev/null 2>&1; command -v git >/dev/null 2>&1"
)
_ACCESS_DENIED = re.compile(
    r"authentication failed|could not read (username|password)|repository not found"
    r"|invalid username or token|permission denied|returned error: 40[134]",
    re.IGNORECASE,
)


class InvalidRepositoryError(ValueError):
    """The repository or branch is not something a session may check out."""


@dataclass(frozen=True)
class RepoRef:
    url: str
    slug: str  # owner/name


def parse_repository(raw: str, *, host: str = "github.com") -> RepoRef:
    """Accept ``owner/name`` or an ``https`` URL on ``host``; refuse everything else.

    The URL is passed to ``git clone`` inside the sandbox, so schemes that read the local
    filesystem (``file://``, plain paths) or carry credentials in the URL are refused here.
    """
    text = raw.strip()
    if _SLUG.match(text):
        slug = text
    else:
        parsed = urlparse(text)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != host.lower():
            raise InvalidRepositoryError(f"Use owner/name or an https://{host}/owner/name URL.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise InvalidRepositoryError("The repository URL must not carry credentials or query.")
        slug = parsed.path.strip("/").removesuffix(".git")
        if not _SLUG.match(slug):
            raise InvalidRepositoryError(f"Use owner/name or an https://{host}/owner/name URL.")
    return RepoRef(url=f"https://{host}/{slug}.git", slug=slug)


def is_valid_branch(name: str) -> bool:
    return bool(_BRANCH.match(name)) and ".." not in name and not name.endswith((".lock", "/", "."))


def validate_branch(name: str) -> str:
    cleaned = name.strip()
    if not is_valid_branch(cleaned):
        raise InvalidRepositoryError(f"'{name}' is not a usable branch name.")
    return cleaned


async def ensure_git_available(driver: SandboxDriverProtocol, session_id: str) -> str | None:
    """Make sure ``git`` exists in the sandbox; returns why not, or ``None`` when it does."""
    result = await driver.exec_command(
        session_id, _ENSURE_GIT, timeout_seconds=INSTALL_TIMEOUT_SECONDS
    )
    if result.exit_code == 0:
        return None
    return (
        "git is not installed in this sandbox image and could not be installed "
        "(a non-root sandbox cannot install packages); use an image that includes git"
    )


def _folder_for(repo: RepoRef, directory: str | None) -> str:
    folder = (directory or repo.slug.rsplit("/", 1)[-1]).strip()
    if not _FOLDER.match(folder) or ".." in folder:
        raise InvalidRepositoryError(
            f"'{folder}' is not a usable folder name; use a single plain name like 'my-app'."
        )
    return f"{WORKSPACE}/{folder}"


_SUMMARY = (
    "git rev-parse --abbrev-ref HEAD; git log -1 --format='%h %s (%cr)'; echo '--- top level'; "
    "ls -1A | head -40"
)

_NO_ACCESS = (
    "GitHub refused access to {slug}. Either the name is wrong or no credential with access "
    "to it is available to this sandbox (the member's linked GitHub account or the GitHub App "
    "installation must be able to read it). Do not retry or hunt for tokens. Check the name "
    "with github_find_repositories; if it still fails, carry on without that repository where "
    "you can, and say in your answer which repository you needed and that the user should link "
    "GitHub in Settings > Git & version control."
)


@dataclass(frozen=True)
class CloneOutcome:
    """What a clone attempt produced: the text the agent reads and, on success, where."""

    message: str
    folder: str | None = None


async def clone_repository(
    driver: SandboxDriverProtocol,
    session_id: str,
    repository: str,
    branch: str | None = None,
    directory: str | None = None,
) -> str:
    return (await clone_into_workspace(driver, session_id, repository, branch, directory)).message


async def clone_into_workspace(
    driver: SandboxDriverProtocol,
    session_id: str,
    repository: str,
    branch: str | None = None,
    directory: str | None = None,
) -> CloneOutcome:
    """Clone ``repository`` into ``/workspace/<name>`` and describe what is there.

    Idempotent: an existing clone is refreshed with ``git fetch`` instead of failing, so the
    agent can call this whenever it is unsure the code is present. Returns a plain-language
    result (errors included) because the agent, not a person, reads it.
    """
    try:
        repo = parse_repository(repository)
        branch_name = validate_branch(branch) if branch else None
        target = _folder_for(repo, directory)
    except InvalidRepositoryError as err:
        return CloneOutcome(f"Error: {err}")
    if (missing := await ensure_git_available(driver, session_id)) is not None:
        return CloneOutcome(f"Error: {missing}.")

    branch_option = f"--branch {shlex.quote(branch_name)} " if branch_name else ""
    folder = shlex.quote(target)
    command = (
        f"if git -C {folder} rev-parse --is-inside-work-tree >/dev/null 2>&1; then "
        f"echo ALREADY_CLONED; git -C {folder} fetch --depth 50 origin 2>&1 | tail -3; "
        f"else git clone --depth 50 {branch_option}{shlex.quote(repo.url)} {folder} 2>&1; fi "
        # Grouped: without the parentheses the summary's own `;` would run even after a failed clone.
        f"&& cd {folder} && ( {_SUMMARY} )"
    )
    result = await driver.exec_command(session_id, command, timeout_seconds=CLONE_TIMEOUT_SECONDS)
    output = (result.stdout or "") + (result.stderr or "")
    if result.exit_code != 0:
        logger.warning("Agent clone of %s in %s failed: %s", repo.slug, session_id, output[-300:])
        if _ACCESS_DENIED.search(output):
            return CloneOutcome(_NO_ACCESS.format(slug=repo.slug))
        return CloneOutcome(
            f"Error: could not clone {repo.slug}.\n{clamp_output(output.strip(), 1500)}"
        )
    already = output.startswith("ALREADY_CLONED")
    body = output.removeprefix("ALREADY_CLONED").strip()
    head = "Already cloned (fetched the latest)" if already else "Cloned"
    return CloneOutcome(
        f"{head} {repo.slug} into {target}\nbranch / last commit / top level:\n{body}", target
    )
