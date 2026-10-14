"""Publishing a branch from a sandbox: the one place that holds write credentials.

Two paths, chosen by ``PublishSpec.signing_mode``:

* a plain ``git push`` of the commits the sandbox made (already signed, or not, locally);
* ``GITHUB_APP``: the branch's new commits are replayed through GitHub's GraphQL
  ``createCommitOnBranch`` mutation, which GitHub signs. With a GitHub App token the commit
  shows as verified and made by the App; with a member's own token, as that member.

The sandbox never receives a write token for either: it is passed to the one push command, or
used here, outside the sandbox.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from specifications.interfaces.git import PublishResult, PublishSpec, SigningMode
from specifications.interfaces.sandbox import ExecResult, SandboxDriverProtocol

API_TIMEOUT_SECONDS = 30.0
# createCommitOnBranch takes the file contents inline; stay well under GitHub's request limit.
MAX_COMMIT_PAYLOAD_BYTES = 20 * 1024 * 1024
REMOTE = re.compile(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$")
SYMLINK_MODE, SUBMODULE_MODE, EXECUTABLE_MODE = "120000", "160000", "100755"

WORKSPACE = "/workspace"
REPOSITORY_SEARCH_DEPTH = 3
AppTokenMinter = Callable[[str, int | None], Awaitable[str | None]]


class PublishError(RuntimeError):
    """The branch could not be published; the message says why and what to do."""


@dataclass(frozen=True)
class _FileChange:
    status: str  # "A", "M" or "D"
    path: str
    mode: str
    blob: str


def graphql_url(api_url: str) -> str:
    """GitHub.com serves GraphQL beside the REST API; Enterprise Server under ``/api/graphql``."""
    base = api_url.rstrip("/")
    return base[: -len("/v3")] + "/graphql" if base.endswith("/api/v3") else f"{base}/graphql"


def repository_of(remote_url: str) -> str:
    match = REMOTE.search(remote_url.strip())
    if match is None:
        raise PublishError("The workspace has no GitHub 'origin' remote to publish to.")
    return f"{match.group(1)}/{match.group(2)}"


class BranchPublisher:
    """Publishes the working branch of one sandbox session."""

    def __init__(
        self,
        driver: SandboxDriverProtocol,
        api_url: str,
        mint_app_token: AppTokenMinter,
    ) -> None:
        self._driver = driver
        self._api_url = api_url.rstrip("/")
        self._mint_app_token = mint_app_token
        self._cwd = WORKSPACE

    async def publish(self, session_id: str, spec: PublishSpec) -> PublishResult:
        self._cwd = await self._repository_dir(session_id, spec.path)
        branch = spec.branch or await self._current_branch(session_id)
        repository = repository_of(
            await self._git(session_id, "git config --get remote.origin.url")
        )
        if spec.signing_mode == SigningMode.GITHUB_APP:
            return await self._replay_through_api(session_id, repository, branch, spec)
        return await self._push(session_id, repository, branch, spec)

    async def _git(self, session_id: str, command: str, env: dict[str, str] | None = None) -> str:
        result = await self._run(session_id, command, env)
        if result.exit_code != 0:
            detail = (result.stderr or result.stdout or "no output").strip()
            raise PublishError(f"`{command.split(' | ')[0]}` failed: {detail}")
        return result.stdout.strip()

    async def _run(
        self, session_id: str, command: str, env: dict[str, str] | None = None
    ) -> ExecResult:
        return await self._driver.exec_command(
            session_id, command, workdir=self._cwd, timeout_seconds=300, env=env
        )

    async def _repository_dir(self, session_id: str, requested: str) -> str:
        """The folder holding the repository: named, /workspace itself, or the one under it.

        ``clone_repository`` puts a repository in ``/workspace/<name>``, so the sandbox's own
        working directory is usually not the repository.
        """
        if requested:
            return requested if requested.startswith("/") else f"{WORKSPACE}/{requested.strip('/')}"
        inside = await self._driver.exec_command(
            session_id, "git rev-parse --show-toplevel", workdir=WORKSPACE, timeout_seconds=30
        )
        if inside.exit_code == 0 and inside.stdout.strip():
            return inside.stdout.strip()
        found = await self._driver.exec_command(
            session_id,
            f"find . -maxdepth {REPOSITORY_SEARCH_DEPTH} -name .git -not -path '*/node_modules/*'"
            " -prune -print",
            workdir=WORKSPACE,
            timeout_seconds=30,
        )
        folders = sorted(
            line[2:].removesuffix("/.git") for line in found.stdout.splitlines() if "/.git" in line
        )
        if len(folders) == 1:
            return f"{WORKSPACE}/{folders[0]}"
        if not folders:
            raise PublishError(f"There is no git repository under {WORKSPACE}: clone one first.")
        raise PublishError(
            "There are several repositories under /workspace ("
            + ", ".join(folders)
            + "): say which one with the path argument."
        )

    async def _current_branch(self, session_id: str) -> str:
        branch = await self._git(session_id, "git branch --show-current")
        if not branch:
            raise PublishError("No branch is checked out (detached HEAD); create or switch to one.")
        return branch

    async def _write_token(self, repository: str, spec: PublishSpec) -> tuple[str, str | None]:
        """The credential to publish with, and a notice when it is not the usual one."""
        if spec.token:
            return spec.token, None
        installation = next(
            (i for i, login in spec.installations if repository.startswith(f"{login}/")), None
        )
        token = await self._mint_app_token(repository, installation)
        if token:
            return token, None
        if spec.fallback_token:
            return spec.fallback_token, (
                "Published with your GitHub account: no GitHub App installation can write to "
                f"{repository} (map the App's installation to your organization under Settings > "
                "GitHub to have the App publish instead)."
            )
        raise PublishError(
            f"No GitHub credential can write to {repository}. The GitHub App gave no token (is its "
            "installation mapped to your organization under Settings > GitHub, with 'Contents: "
            "Read & write' on the repository?) and you have no linked GitHub account: link one "
            "under Settings > Git & version control."
        )

    async def _push(
        self, session_id: str, repository: str, branch: str, spec: PublishSpec
    ) -> PublishResult:
        token, notice = await self._write_token(repository, spec)
        # The credential helper reads the token from the command's own environment, so it is
        # present for this push only.
        env = {
            "GITHUB_TOKEN": token,
            "GH_TOKEN": token,
            "GIT_TERMINAL_PROMPT": "0",
            "ROCKET_PUBLISH": "1",
        }
        force = " --force-with-lease" if spec.force_with_lease else ""
        await self._git(session_id, f"git push{force} -u origin {shlex.quote(branch)}", env=env)
        head = await self._git(session_id, "git rev-parse HEAD")
        return PublishResult(
            repository, branch, head, via="git_push", notices=[notice] if notice else []
        )

    async def _replay_through_api(
        self, session_id: str, repository: str, branch: str, spec: PublishSpec
    ) -> PublishResult:
        token, token_notice = await self._write_token(repository, spec)
        await self._fetch_branch(session_id, branch, required=False)
        remote_head, created = await self._remote_head(session_id, repository, branch, spec, token)
        commits = (
            await self._git(session_id, f"git rev-list --reverse {remote_head}..HEAD")
        ).split()
        if not commits:
            return PublishResult(repository, branch, remote_head, "github_api", commits=0)
        merges = await self._git(session_id, f"git rev-list --merges {remote_head}..HEAD")
        if merges:
            raise PublishError(
                "The branch contains merge commits, which GitHub-signed publishing cannot "
                "replay. Rebase onto the base branch (git rebase) and publish again."
            )

        notices: list[str] = [token_notice] if token_notice else []
        head = remote_head
        for sha in commits:
            head = await self._create_commit(
                session_id, repository, branch, sha, head, token, notices
            )
        await self._resync(session_id, branch)
        if created:
            notices.append(f"Created the branch {branch} on GitHub.")
        return PublishResult(
            repository, branch, head, "github_api", commits=len(commits), notices=notices
        )

    async def _remote_head(
        self, session_id: str, repository: str, branch: str, spec: PublishSpec, token: str
    ) -> tuple[str, bool]:
        existing = await self._run(
            session_id, f"git rev-parse --verify --quiet refs/remotes/origin/{shlex.quote(branch)}"
        )
        if existing.exit_code == 0 and existing.stdout.strip():
            head = existing.stdout.strip()
            behind = await self._run(session_id, f"git merge-base --is-ancestor {head} HEAD")
            if behind.exit_code != 0:
                raise PublishError(
                    f"origin/{branch} has commits this branch does not: fetch and rebase "
                    "(git rebase origin/" + branch + "), then publish again."
                )
            return head, False
        default = await self._default_branch(session_id, spec.default_branch)
        await self._fetch_branch(session_id, default, required=True)
        base = await self._git(
            session_id, f"git merge-base HEAD refs/remotes/origin/{shlex.quote(default)}"
        )
        await self._rest(
            "POST",
            f"/repos/{repository}/git/refs",
            token,
            {"ref": f"refs/heads/{branch}", "sha": base},
        )
        return base, True

    async def _create_commit(
        self,
        session_id: str,
        repository: str,
        branch: str,
        sha: str,
        expected_head: str,
        token: str,
        notices: list[str],
    ) -> str:
        changes = await self._changes(session_id, sha)
        message = await self._git(session_id, f"git log -1 --format=%B {sha}")
        additions: list[dict[str, str]] = []
        deletions: list[dict[str, str]] = []
        size = 0
        for change in changes:
            if change.status == "D":
                deletions.append({"path": change.path})
                continue
            self._check_representable(change, notices)
            encoded = await self._git(session_id, f"git cat-file blob {change.blob} | base64 -w0")
            size += len(encoded)
            if size > MAX_COMMIT_PAYLOAD_BYTES:
                raise PublishError(
                    f"Commit {sha[:8]} is too large to publish through GitHub's API "
                    f"(over {MAX_COMMIT_PAYLOAD_BYTES // (1024 * 1024)} MB of file contents). "
                    "Split it into smaller commits."
                )
            additions.append({"path": change.path, "contents": encoded})
        headline, _, body = message.partition("\n")
        data = await self._graphql(
            token,
            {
                "branch": {"repositoryNameWithOwner": repository, "branchName": branch},
                "message": {"headline": headline.strip(), "body": body.strip()},
                "fileChanges": {"additions": additions, "deletions": deletions},
                "expectedHeadOid": expected_head,
            },
        )
        return data

    @staticmethod
    def _check_representable(change: _FileChange, notices: list[str]) -> None:
        if change.mode in (SYMLINK_MODE, SUBMODULE_MODE):
            kind = "symbolic link" if change.mode == SYMLINK_MODE else "submodule"
            raise PublishError(
                f"{change.path} is a {kind}, which GitHub's signed-commit API cannot create. "
                "Remove it from the commit, or ask an administrator to use GPG signing."
            )
        if change.status == "A" and change.mode == EXECUTABLE_MODE:
            notices.append(
                f"{change.path} was published without its executable bit (GitHub's API cannot "
                "set it); run `chmod +x` and commit again if it must be executable."
            )

    async def _changes(self, session_id: str, sha: str) -> list[_FileChange]:
        raw = await self._git(
            session_id, f"git diff-tree -r --no-renames --no-commit-id --raw -z {sha}"
        )
        fields = raw.split("\0")
        changes: list[_FileChange] = []
        for meta, path in zip(fields[0::2], fields[1::2], strict=False):
            parts = meta.lstrip(":").split()
            if len(parts) < 5:
                continue
            _, new_mode, _, new_blob, status = parts[:5]
            if status not in ("A", "M", "D"):
                raise PublishError(
                    f"{path}: a '{status}' change cannot be replayed through GitHub's API."
                )
            changes.append(_FileChange(status, path, new_mode, new_blob))
        return changes

    async def _fetch_branch(self, session_id: str, branch: str, *, required: bool) -> None:
        """Bring one remote branch into ``origin/<branch>``.

        An explicit refspec, because ``clone_repository`` makes a shallow single-branch clone
        whose fetch configuration only tracks the default branch: a plain ``git fetch origin
        <branch>`` would update FETCH_HEAD and leave ``origin/<branch>`` unknown.
        """
        quoted = shlex.quote(branch)
        command = f"git fetch --quiet origin +refs/heads/{quoted}:refs/remotes/origin/{quoted}"
        if required:
            await self._git(session_id, command)
        else:
            await self._run(session_id, command)

    async def _default_branch(self, session_id: str, configured: str) -> str:
        """The repository's own default branch (from the clone), else the organization's setting."""
        result = await self._run(session_id, "git symbolic-ref --short refs/remotes/origin/HEAD")
        name = result.stdout.strip().removeprefix("origin/")
        return name if result.exit_code == 0 and name else configured

    async def _resync(self, session_id: str, branch: str) -> None:
        """Move the local branch onto the commits GitHub created, keeping uncommitted work."""
        quoted = shlex.quote(branch)
        await self._fetch_branch(session_id, branch, required=True)
        await self._git(
            session_id,
            f"git reset --quiet --mixed origin/{quoted}"
            f" && (git branch --set-upstream-to=origin/{quoted} >/dev/null 2>&1 || true)",
        )

    async def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Rocket-Chat",
        }

    async def _rest(self, method: str, path: str, token: str, body: dict[str, str]) -> None:
        async with httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS) as client:
            response = await client.request(
                method, f"{self._api_url}{path}", headers=await self._headers(token), json=body
            )
        if response.status_code not in (200, 201):
            raise PublishError(_describe_failure(response))

    async def _graphql(self, token: str, commit_input: dict[str, object]) -> str:
        query = (
            "mutation($input: CreateCommitOnBranchInput!) {"
            " createCommitOnBranch(input: $input) { commit { oid } } }"
        )
        async with httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS) as client:
            response = await client.post(
                graphql_url(self._api_url),
                headers=await self._headers(token),
                json={"query": query, "variables": {"input": commit_input}},
            )
        payload = response.json() if response.content else {}
        if response.status_code != 200 or payload.get("errors"):
            detail = "; ".join(e.get("message", "") for e in payload.get("errors") or []) or (
                _describe_failure(response)
            )
            raise PublishError(f"GitHub refused the commit: {detail}")
        oid = ((payload.get("data") or {}).get("createCommitOnBranch") or {}).get("commit", {})
        if not oid.get("oid"):
            raise PublishError("GitHub did not return the new commit.")
        return str(oid["oid"])


def _describe_failure(response: httpx.Response) -> str:
    text = response.text.strip()[:300]
    if response.status_code in (401, 403, 404):
        return (
            f"GitHub answered {response.status_code}: the credential cannot write to this "
            f"repository (the GitHub App needs 'Contents: Read & write'). {text}"
        )
    return f"GitHub answered {response.status_code}: {text}"
