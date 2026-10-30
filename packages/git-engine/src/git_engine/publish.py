"""Publishing a branch from a sandbox: the one place that holds write credentials.

The sandbox holds no credential. It only works out what to publish (with plain git, its fetches
going through the read-only git gateway) and packs the branch's new commits into a bundle. The
platform imports that bundle into a throwaway repository (``host_repo.HostRepository``) and,
by ``PublishSpec.signing_mode``:

* ``GITHUB_APP``: replays the commits through GitHub's GraphQL ``createCommitOnBranch``
  mutation, which GitHub signs (verified as the App, or as the member with their own token);
* ``GPG_KEY`` with a key: re-creates them signed with the member's key, then pushes;
* otherwise: pushes them as they are.

When the published commits differ from the sandbox's (re-created), the sandbox's branch is moved
onto them, keeping uncommitted work. Nothing the agent can configure in the sandbox (hooks,
credential helpers, push URLs, ``insteadOf``) takes part in the push.
"""

from __future__ import annotations

import base64
import re
import shlex
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
from sandbox_driver.blobs import BlobTooLargeError, read_bytes
from sandbox_driver.workspace import RepositoryNotFoundError, resolve_repository_dir

from git_engine.config import github_web_url
from git_engine.host_repo import HostGitError, HostRepository
from git_engine.trailers import format_commit_message
from specifications.interfaces.git import CoAuthor, PublishResult, PublishSpec, SigningMode
from specifications.interfaces.sandbox import ExecResult, SandboxDriverProtocol

API_TIMEOUT_SECONDS = 30.0
SANDBOX_TIMEOUT_SECONDS = 300
# createCommitOnBranch takes the file contents inline; stay well under GitHub's request limit.
MAX_COMMIT_PAYLOAD_BYTES = 20 * 1024 * 1024
MAX_BUNDLE_BYTES = 200 * 1024 * 1024
SYMLINK_MODE, SUBMODULE_MODE, EXECUTABLE_MODE = "120000", "160000", "100755"
_SLUG_PART = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}"
_PATH = re.compile(rf"^/?({_SLUG_PART})/({_SLUG_PART}?)(?:\.git)?/?$")
_NESTED_PATH = re.compile(rf"^/?({_SLUG_PART}(?:/{_SLUG_PART})*)/({_SLUG_PART}?)(?:\.git)?/?$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")

AppTokenMinter = Callable[[str, int | None], Awaitable[str | None]]


# What git prints when the platform's gateway (or GitHub) refuses the sandbox's credential.
_CREDENTIAL_FAILURE_MARKERS = (
    "invalid or expired git gateway credential",
    "authentication failed",
    "could not read username",
    "returned error: 401",
    "returned error: 403",
)


def _is_credential_failure(output: str) -> bool:
    lowered = output.lower()
    return any(marker in lowered for marker in _CREDENTIAL_FAILURE_MARKERS)


class PublishError(RuntimeError):
    """The branch could not be published; the message says why and what to do."""


@dataclass(frozen=True)
class _FileChange:
    status: str  # "A", "M" or "D"
    path: str
    mode: str
    blob: str


@dataclass(frozen=True)
class _Plan:
    """What is published: the commits after ``base`` (empty: the whole history)."""

    base: str
    commits: list[str]
    created: bool
    # Remote head a forced push may replace (``--force-with-lease``), when it is not an ancestor.
    lease: str | None = None


def graphql_url(api_url: str) -> str:
    """GitHub.com serves GraphQL beside the REST API; Enterprise Server under ``/api/graphql``."""
    base = api_url.rstrip("/")
    return base[: -len("/v3")] + "/graphql" if base.endswith("/api/v3") else f"{base}/graphql"


def repository_of(remote_url: str, host: str = "github.com", *, nested: bool = False) -> str:
    """``owner/name`` of an origin URL on ``host`` (https, ssh or scp form), else a refusal.
    ``nested`` accepts GitLab's ``group/subgroup/name``.

    The host is compared exactly: ``https://evil.example/github.com/acme/app`` is not GitHub.
    """
    text = remote_url.strip()
    scp = re.match(r"^git@([^:/]+):(.+)$", text)
    if scp:
        found_host, path = scp.group(1), scp.group(2)
    else:
        parsed = urlparse(text)
        if parsed.scheme not in ("https", "ssh") or parsed.username not in (None, "git"):
            raise PublishError(f"The 'origin' remote is not a GitHub repository on {host}.")
        found_host, path = parsed.hostname or "", parsed.path
    match = (_NESTED_PATH if nested else _PATH).match(path)
    if found_host.lower() != host.lower() or match is None:
        raise PublishError(f"The 'origin' remote is not a repository on {host}.")
    return f"{match.group(1)}/{match.group(2).removesuffix('.git')}"


def _validated_branch(branch: str) -> str:
    if not _BRANCH.match(branch) or ".." in branch or branch.endswith((".lock", "/", ".")):
        raise PublishError(f"'{branch}' is not a branch name that can be published.")
    return branch


class _Sandbox:
    """The sandbox side of a publication: credential-free git in the repository folder."""

    def __init__(self, driver: SandboxDriverProtocol, session_id: str, folder: str) -> None:
        self.driver, self.session_id, self.folder = driver, session_id, folder

    async def run(self, command: str) -> ExecResult:
        return await self.driver.exec_command(
            self.session_id, command, workdir=self.folder, timeout_seconds=SANDBOX_TIMEOUT_SECONDS
        )

    async def git(self, command: str) -> str:
        result = await self.run(command)
        if result.exit_code != 0:
            detail = (result.stderr or result.stdout or "no output").strip()
            raise PublishError(f"`{command.split(' | ')[0]}` failed: {detail}")
        return result.stdout.strip()

    async def fetch_branch(self, branch: str, *, required: bool) -> bool:
        """Bring one remote branch into ``origin/<branch>``; whether it exists on the remote.

        An explicit refspec, because ``clone_repository`` makes a single-branch clone whose fetch
        configuration only tracks the default branch. A branch that no longer exists on the
        remote (deleted after its pull request merged) leaves no stale tracking ref behind.
        """
        quoted = shlex.quote(branch)
        command = f"git fetch --quiet origin +refs/heads/{quoted}:refs/remotes/origin/{quoted}"
        result = await self.run(command)
        if result.exit_code != 0 and _is_credential_failure(result.stderr + result.stdout):
            # The sandbox renews its gateway credential before each command, so one more try
            # is enough for a capability that expired a moment ago.
            result = await self.run(command)
            if result.exit_code != 0 and _is_credential_failure(result.stderr + result.stdout):
                raise PublishError(
                    "The sandbox's git credential for the platform was refused "
                    f"({(result.stderr or result.stdout).strip()[:200]}). This is not a "
                    "problem with the repository or the branch: report it to the person instead "
                    "of working around it."
                )
        if result.exit_code == 0:
            return True
        if required:
            detail = (result.stderr or result.stdout or "no output").strip()
            raise PublishError(f"`git fetch` of {branch} failed: {detail}")
        if "couldn't find remote ref" in (result.stderr + result.stdout).lower():
            await self.run(f"git update-ref -d refs/remotes/origin/{quoted}")
        return False

    async def bundle(self, base: str) -> bytes:
        path = f"/tmp/rocket-publish-{uuid.uuid4().hex}.bundle"
        try:
            exclude = f" ^{shlex.quote(base)}" if base else ""
            await self.git(f"git bundle create --quiet {path} HEAD{exclude}")
            return await read_bytes(self.driver, self.session_id, path, max_bytes=MAX_BUNDLE_BYTES)
        except BlobTooLargeError as err:
            raise PublishError(
                f"The new commits are too large to publish ({err}). Split the work or remove "
                "large generated files from the commits."
            ) from err
        finally:
            await self.run(f"rm -f {path}")

    async def resync(self, branch: str) -> None:
        """Move the local branch onto the published commits, keeping uncommitted work."""
        quoted = shlex.quote(branch)
        await self.fetch_branch(branch, required=True)
        await self.git(
            f"git reset --quiet --mixed origin/{quoted}"
            f" && (git branch --set-upstream-to=origin/{quoted} >/dev/null 2>&1 || true)"
        )


@dataclass(frozen=True)
class OtherForge:
    """A forge besides GitHub the publisher pushes to (GitLab), chosen by the origin's host.
    It has no App to re-create commits: they are pushed as they are (or signed with the
    member's key), with the member's token or ``token``."""

    kind: str
    web_url: str
    basic_user: str
    token: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class _Target:
    kind: str
    host: str
    base_url: str
    basic_user: str
    fallback_token: str | None = field(default=None, repr=False)

    @property
    def label(self) -> str:
        return "GitLab" if self.kind == "gitlab" else "GitHub"


class BranchPublisher:
    """Publishes the working branch of one sandbox session."""

    def __init__(
        self,
        driver: SandboxDriverProtocol,
        api_url: str,
        mint_app_token: AppTokenMinter,
        git_base_url: str | None = None,
        *,
        others: tuple[OtherForge, ...] = (),
    ) -> None:
        self._driver = driver
        self._api_url = api_url.rstrip("/")
        self._mint_app_token = mint_app_token
        web = github_web_url(api_url)
        # Where the platform pushes and fetches: GitHub's web host (tests point it elsewhere).
        self._github = _Target(
            "github",
            urlparse(web).hostname or "github.com",
            (git_base_url or web).rstrip("/"),
            "x-access-token",
        )
        self._others = others

    def _target(self, remote_url: str) -> _Target:
        """The forge ``remote_url`` is on: one of the others when its host is theirs."""
        scp = re.match(r"^git@([^:/]+):", remote_url.strip())
        host = (scp.group(1) if scp else urlparse(remote_url.strip()).hostname or "").lower()
        for other in self._others:
            if host and host == (urlparse(other.web_url).hostname or "").lower():
                return _Target(
                    other.kind, host, other.web_url.rstrip("/"), other.basic_user, other.token
                )
        return self._github

    async def publish(self, session_id: str, spec: PublishSpec) -> PublishResult:
        sandbox = _Sandbox(self._driver, session_id, await self._repository_dir(session_id, spec))
        branch = _validated_branch(spec.branch or await self._current_branch(sandbox))
        remote = await sandbox.git("git config --get remote.origin.url")
        target = self._target(remote)
        repository = repository_of(remote, target.host, nested=target.kind != "github")
        plan = await self._plan(sandbox, branch, spec)
        default_branch = await self._default_branch(sandbox, spec.default_branch)
        replays = spec.signing_mode == SigningMode.GITHUB_APP and target.kind == "github"
        via = "github_api" if replays else "git_push"
        if not plan.commits:
            return PublishResult(
                repository,
                branch,
                plan.base,
                via,
                commits=0,
                default_branch=default_branch,
                forge=target.kind,
            )
        if replays:
            await self._refuse_merges(sandbox, plan.base)
        token, notice = await self._write_token(repository, spec, target)
        notices = [notice] if notice else []
        rewritten: list[tuple[str, str]] = []
        bundle = await sandbox.bundle(plan.base)
        try:
            async with HostRepository(
                f"{target.base_url}/{repository}.git", token, basic_user=target.basic_user
            ) as host:
                if plan.base:
                    await host.fetch_base(plan.base)
                tip = await host.import_bundle(bundle)
                if replays:
                    head = await self._replay(
                        host, repository, branch, plan, token, notices, spec.co_authors, rewritten
                    )
                else:
                    head = await self._push(host, branch, plan, spec, tip, notices, rewritten)
        except HostGitError as err:
            raise PublishError(str(err)) from err
        if head != plan.commits[-1]:
            await sandbox.resync(branch)
        else:
            await sandbox.fetch_branch(branch, required=False)
        if plan.created:
            notices.append(f"Created the branch {branch} on {target.label}.")
        return PublishResult(
            repository,
            branch,
            head,
            via,
            commits=len(plan.commits),
            notices=notices,
            rewritten=rewritten,
            default_branch=default_branch,
            forge=target.kind,
        )

    async def _repository_dir(self, session_id: str, spec: PublishSpec) -> str:
        try:
            return await resolve_repository_dir(self._driver, session_id, spec.path or None)
        except RepositoryNotFoundError as err:
            raise PublishError(str(err)) from err

    @staticmethod
    async def _current_branch(sandbox: _Sandbox) -> str:
        branch = await sandbox.git("git branch --show-current")
        if not branch:
            raise PublishError("No branch is checked out (detached HEAD); create or switch to one.")
        return branch

    async def _plan(self, sandbox: _Sandbox, branch: str, spec: PublishSpec) -> _Plan:
        """The base the new commits build on, the commits, and whether the branch is new."""
        quoted = shlex.quote(branch)
        if await sandbox.fetch_branch(branch, required=False):
            remote = await sandbox.git(f"git rev-parse refs/remotes/origin/{quoted}")
            ancestor = await sandbox.run(f"git merge-base --is-ancestor {remote} HEAD")
            if ancestor.exit_code == 0:
                return await self._commits(sandbox, remote, created=False)
            if not spec.force_with_lease:
                raise PublishError(
                    f"origin/{branch} has commits this branch does not: fetch and rebase "
                    f"(git rebase origin/{branch}), then publish again."
                )
            base = await self._detect_base(sandbox, branch, spec, fallback=remote)
            plan = await self._commits(sandbox, base, created=False)
            return _Plan(plan.base, plan.commits, created=False, lease=remote)
        default = await self._default_branch(sandbox, spec.default_branch)
        if not await sandbox.fetch_branch(default, required=False):
            # An empty repository (just created on GitHub): the whole branch is new.
            if spec.signing_mode == SigningMode.GITHUB_APP:
                raise PublishError(
                    f"The repository has no {default} branch yet, and GitHub-signed commits "
                    "can only be added to an existing branch. Create the repository with an "
                    "initial commit (a README) on GitHub, then publish again."
                )
            return await self._commits(sandbox, "", created=True)
        base = await self._detect_base(
            sandbox, branch, spec, fallback=f"refs/remotes/origin/{shlex.quote(default)}"
        )
        return await self._commits(sandbox, base, created=True)

    async def _candidate_base_branches(
        self, sandbox: _Sandbox, branch: str, spec: PublishSpec
    ) -> list[str]:
        candidates: list[str] = []
        upstream_res = await sandbox.run(
            f"git rev-parse --abbrev-ref {shlex.quote(branch)}@{{upstream}}"
        )
        if upstream_res.exit_code == 0:
            up = upstream_res.stdout.strip().removeprefix("origin/")
            if up and up != branch:
                candidates.append(up)
        default = await self._default_branch(sandbox, spec.default_branch)
        for name in (
            default,
            spec.default_branch,
            "main",
            "master",
            "beta",
            "develop",
            "dev",
            "staging",
        ):
            if name and name not in candidates and name != branch:
                candidates.append(name)
        remote_refs = await sandbox.run(
            "git for-each-ref --format=%(refname:strip=3) refs/remotes/origin/"
        )
        if remote_refs.exit_code == 0:
            for line in remote_refs.stdout.splitlines():
                ref = line.strip()
                if ref and ref != "HEAD" and ref != branch and ref not in candidates:
                    candidates.append(ref)
        return candidates

    async def _detect_base(
        self, sandbox: _Sandbox, branch: str, spec: PublishSpec, fallback: str = ""
    ) -> str:
        candidates = await self._candidate_base_branches(sandbox, branch, spec)
        best_base = ""
        for name in candidates:
            res = await sandbox.run(f"git merge-base HEAD refs/remotes/origin/{shlex.quote(name)}")
            if res.exit_code != 0 or not res.stdout.strip():
                continue
            mb = res.stdout.strip()
            if not best_base:
                best_base = mb
                continue
            if mb == best_base:
                continue
            is_anc = await sandbox.run(
                f"git merge-base --is-ancestor {shlex.quote(best_base)} {shlex.quote(mb)}"
            )
            if is_anc.exit_code == 0:
                best_base = mb
        if best_base:
            return best_base
        if fallback:
            res = await sandbox.run(f"git merge-base HEAD {fallback}")
            if res.exit_code == 0 and res.stdout.strip():
                return res.stdout.strip()
        return ""

    @staticmethod
    async def _commits(sandbox: _Sandbox, base: str, *, created: bool) -> _Plan:
        span = f"{shlex.quote(base)}..HEAD" if base else "HEAD"
        listed = await sandbox.git(f"git rev-list --reverse {span}")
        return _Plan(base, listed.split(), created)

    @staticmethod
    async def _default_branch(sandbox: _Sandbox, configured: str) -> str:
        """The repository's own default branch (from the clone), else the organization's setting."""
        result = await sandbox.run("git symbolic-ref --short refs/remotes/origin/HEAD")
        name = result.stdout.strip().removeprefix("origin/")
        return name if result.exit_code == 0 and name else configured

    @staticmethod
    async def _refuse_merges(sandbox: _Sandbox, base: str) -> None:
        span = f"{shlex.quote(base)}..HEAD" if base else "HEAD"
        if await sandbox.git(f"git rev-list --merges {span}"):
            raise PublishError(
                "The branch contains merge commits, which GitHub-signed publishing cannot "
                "replay. Rebase onto the base branch (git rebase) and publish again."
            )

    async def _write_token(
        self, repository: str, spec: PublishSpec, target: _Target
    ) -> tuple[str, str | None]:
        """The credential to publish with, and a notice when it is not the usual one."""
        if target.kind == "gitlab":
            token = spec.gitlab_token or target.fallback_token
            if not token:
                raise PublishError(
                    f"No GitLab credential can write to {repository}: add a GitLab token under "
                    "Settings > Git & version control."
                )
            return token, None
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
        self,
        host: HostRepository,
        branch: str,
        plan: _Plan,
        spec: PublishSpec,
        tip: str,
        notices: list[str],
        rewritten: list[tuple[str, str]],
    ) -> str:
        if spec.signing_mode == SigningMode.GPG_KEY and spec.signing_key is not None:
            mapped = await host.resign(plan.commits, spec.signing_key)
            rewritten.extend((sha, mapped[sha]) for sha in plan.commits)
            tip = mapped[plan.commits[-1]]
            notices.append("Signed with your GPG key on publication.")
        await host.push(branch, lease=plan.lease)
        return tip

    async def _replay(
        self,
        host: HostRepository,
        repository: str,
        branch: str,
        plan: _Plan,
        token: str,
        notices: list[str],
        co_authors: tuple[CoAuthor, ...] = (),
        rewritten: list[tuple[str, str]] | None = None,
    ) -> str:
        staging_branch: str | None = None
        if plan.lease:
            # Replaying directly on the PR branch requires setting the branch ref to plan.base
            # first, which causes GitHub to observe head == base (0 commits) and auto-close the PR!
            # Replay onto an ephemeral staging branch instead, then atomically update the real
            # branch ref to the final new head.
            staging_branch = f"rocket-staging/{branch}-{uuid.uuid4().hex[:8]}"
            await self._rest(
                "POST",
                f"/repos/{repository}/git/refs",
                token,
                {"ref": f"refs/heads/{staging_branch}", "sha": plan.base},
            )
            replay_branch = staging_branch
        elif plan.created:
            await self._rest(
                "POST",
                f"/repos/{repository}/git/refs",
                token,
                {"ref": f"refs/heads/{branch}", "sha": plan.base},
            )
            replay_branch = branch
        else:
            replay_branch = branch

        head = plan.base
        try:
            for sha in plan.commits:
                head = await self._create_commit(
                    host, repository, replay_branch, sha, head, token, notices, co_authors
                )
                if rewritten is not None:
                    rewritten.append((sha, head))
            if staging_branch is not None:
                if plan.lease:
                    ref_data = await self._rest(
                        "GET",
                        f"/repos/{repository}/git/refs/heads/{branch}",
                        token,
                    )
                    current_sha = (ref_data.get("object") or {}).get("sha")
                    if current_sha and current_sha != plan.lease:
                        raise PublishError(
                            f"Refusing force-push: {branch} moved on GitHub from {plan.lease[:7]} "
                            f"to {str(current_sha)[:7]} concurrently; fetch and rebase before publishing."
                        )
                await self._rest(
                    "PATCH",
                    f"/repos/{repository}/git/refs/heads/{branch}",
                    token,
                    {"sha": head, "force": True},
                )
        except Exception:
            if plan.created:
                try:
                    await self._rest(
                        "DELETE",
                        f"/repos/{repository}/git/refs/heads/{branch}",
                        token,
                    )
                except Exception:
                    pass
            raise
        finally:
            if staging_branch is not None:
                try:
                    await self._rest(
                        "DELETE",
                        f"/repos/{repository}/git/refs/heads/{staging_branch}",
                        token,
                    )
                except Exception:
                    pass
        return head

    async def _create_commit(
        self,
        host: HostRepository,
        repository: str,
        branch: str,
        sha: str,
        expected_head: str,
        token: str,
        notices: list[str],
        co_authors: tuple[CoAuthor, ...] = (),
    ) -> str:
        additions: list[dict[str, str]] = []
        deletions: list[dict[str, str]] = []
        size = 0
        for change in await self._changes(host, sha):
            if change.status == "D":
                deletions.append({"path": change.path})
                continue
            self._check_representable(change, notices)
            encoded = base64_text(await host.blob(change.blob))
            size += len(encoded)
            if size > MAX_COMMIT_PAYLOAD_BYTES:
                raise PublishError(
                    f"Commit {sha[:8]} is too large to publish through GitHub's API "
                    f"(over {MAX_COMMIT_PAYLOAD_BYTES // (1024 * 1024)} MB of file contents). "
                    "Split it into smaller commits."
                )
            additions.append({"path": change.path, "contents": encoded})
        message = format_commit_message(
            await host.text("log", "-1", "--format=%B", sha), list(co_authors)
        )
        headline, _, body = message.partition("\n")
        return await self._graphql(
            token,
            {
                "branch": {"repositoryNameWithOwner": repository, "branchName": branch},
                "message": {"headline": headline.strip(), "body": body.strip()},
                "fileChanges": {"additions": additions, "deletions": deletions},
                "expectedHeadOid": expected_head,
            },
        )

    @staticmethod
    def _check_representable(change: _FileChange, notices: list[str]) -> None:
        if change.mode in (SYMLINK_MODE, SUBMODULE_MODE):
            kind = "symbolic link" if change.mode == SYMLINK_MODE else "submodule"
            raise PublishError(
                f"{change.path} is a {kind}, which GitHub's signed-commit API cannot create. "
                "Remove it from the commit, or ask an administrator to use GPG signing."
            )
        if change.status in ("A", "M") and change.mode == EXECUTABLE_MODE:
            notices.append(
                f"{change.path} was published without its executable bit (GitHub's API cannot "
                "set it); run `chmod +x` and commit again if it must be executable."
            )

    @staticmethod
    async def _changes(host: HostRepository, sha: str) -> list[_FileChange]:
        raw = await host.text(
            "diff-tree", "-r", "--no-renames", "--no-commit-id", "--raw", "-z", sha
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

    async def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Rocket-Chat",
        }

    async def _rest(
        self, method: str, path: str, token: str, body: dict[str, object] | None = None
    ) -> Any:
        async with httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS) as client:
            response = await client.request(
                method, f"{self._api_url}{path}", headers=await self._headers(token), json=body
            )
        if response.status_code not in (200, 201, 204):
            raise PublishError(_describe_failure(response))
        return response.json() if response.content else {}

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


def base64_text(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _describe_failure(response: httpx.Response) -> str:
    text = response.text.strip()[:300]
    if response.status_code in (401, 403, 404):
        return (
            f"GitHub answered {response.status_code}: the credential cannot write to this "
            f"repository (the GitHub App needs 'Contents: Read & write'). {text}"
        )
    return f"GitHub answered {response.status_code}: {text}"
