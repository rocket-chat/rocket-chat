"""Git Engine implementing GitEngineProtocol with Co-Authoring, Signing, and Webhooks."""

import fnmatch
import hashlib
import hmac
import json
import logging
import shlex
import time
from collections.abc import Sequence
from typing import Any

import httpx
import jwt
from sandbox_driver.workspace import resolve_repository_dir

from git_engine.config import GitEngineSettings
from git_engine.publish import BranchPublisher, PublishError
from git_engine.trailers import format_commit_message
from specifications.interfaces.git import (
    CommitSpec,
    GitEngineProtocol,
    PublishResult,
    PublishSpec,
    RepoEvent,
    WebhookTrigger,
)
from specifications.interfaces.sandbox import ExecResult, SandboxDriverProtocol

logger = logging.getLogger(__name__)

# GitHub `author_association` values that imply write access to the repository.
_TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


def _installation_id(payload: dict[str, Any]) -> int | None:
    raw = (payload.get("installation") or {}).get("id")
    return int(raw) if isinstance(raw, int) else None


def pick_installation(installations: Sequence[tuple[int, str]], repo: str | None) -> int | None:
    """The mapped installation to use for ``owner/name``: the one on that owner's account, else
    the only one there is; ``None`` when none is mapped or the choice would be a guess."""
    owner = (repo or "").split("/", 1)[0].lower()
    for installation_id, login in installations:
        if owner and login.lower() == owner:
            return installation_id
    return installations[0][0] if len(installations) == 1 else None


class GitEngine(GitEngineProtocol):
    """Governs Git operations, commit signing, and GitHub event ingestion."""

    def __init__(
        self,
        driver: SandboxDriverProtocol | None = None,
        settings: GitEngineSettings | None = None,
    ) -> None:
        self.driver = driver
        self.settings = settings or GitEngineSettings.from_env()

    async def create_commit(
        self,
        session_id: str,
        spec: CommitSpec,
    ) -> str:
        """
        Formats commit message with Co-authored-by trailers, executes signing,
        and creates commit in sandbox workspace. Returns commit SHA.
        """
        formatted_message = format_commit_message(spec.message, spec.co_authors)

        if not self.driver:
            # Fallback mock SHA if no driver attached
            sha = hashlib.sha1(formatted_message.encode("utf-8")).hexdigest()
            return sha

        # The repository is resolved, never created: committing in a folder that is not a
        # repository used to `git init` /workspace and swallow the real clone as a gitlink.
        repository = await resolve_repository_dir(self.driver, session_id, spec.path or None)

        async def run(command: str) -> ExecResult:
            assert self.driver is not None
            return await self.driver.exec_command(session_id, command, workdir=repository)

        # The caller's (policy-resolved) identity, else this deployment's own bot.
        committer_name = spec.committer_name or self.settings.bot_author_name
        committer_email = spec.committer_email or self.settings.bot_author_email
        author_name = spec.author_name or committer_name
        author_email = spec.author_email or committer_email
        await run(
            f"git config user.name {shlex.quote(committer_name)} && "
            f"git config user.email {shlex.quote(committer_email)}"
        )
        if spec.branch:
            checkout = await run(f"git checkout -B {shlex.quote(spec.branch)}")
            if checkout.exit_code != 0:
                raise RuntimeError(f"git checkout failed: {checkout.stderr.strip()}")
        await run("git add -A")

        # Never signed here: the sandbox holds no key. ``gpg_key`` commits are signed on the
        # platform when the branch is published (``host_repo.HostRepository.resign``).
        env_prefix = (
            f"GIT_AUTHOR_NAME={shlex.quote(author_name)} "
            f"GIT_AUTHOR_EMAIL={shlex.quote(author_email)} "
        )
        res = await run(f"{env_prefix}git commit --no-gpg-sign -m {shlex.quote(formatted_message)}")
        if res.exit_code != 0 and "nothing to commit" not in res.stdout + res.stderr:
            raise RuntimeError(f"git commit failed (exit {res.exit_code}): {res.stderr}")
        return (await run("git rev-parse HEAD")).stdout.strip()

    def generate_app_jwt(self) -> str:
        """Generates an RS256 JWT for GitHub App authentication valid for 10 minutes."""
        if not self.settings.github_app_id or not self.settings.github_app_private_key:
            raise ValueError("github_app_id and github_app_private_key must be configured")

        now = int(time.time())
        payload = {
            "iat": now - 60,  # 60s in the past to prevent clock drift issues
            "exp": now + (10 * 60),  # 10 minutes max expiration
            "iss": self.settings.github_app_id,
        }
        pem_key = self.settings.github_app_private_key
        # Handle cases where PEM was escaped with newlines in env vars
        if "\\n" in pem_key:
            pem_key = pem_key.replace("\\n", "\n")

        return jwt.encode(payload, pem_key, algorithm="RS256")

    async def get_github_access_token(
        self,
        repo: str | None = None,
        permissions: dict[str, str] | None = None,
        repositories: list[str] | None = None,
        installation_id: int | None = None,
        inherit_permissions: bool = False,
    ) -> str | None:
        """A GitHub App installation token scoped to ``permissions``, or ``None``.

        ``inherit_permissions`` asks for whatever the installation was granted (asking for a
        permission it lacks makes GitHub refuse the token), still limited to ``repositories``.

        There is no deployment-wide token: work for an organization uses its mapped App
        installation or its member's own linked account, never a credential shared by tenants.
        """
        if not (self.settings.github_app_id and self.settings.github_app_private_key):
            return None

        try:
            app_jwt = self.generate_app_jwt()
            headers = {
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }

            async with httpx.AsyncClient(timeout=15.0) as client:
                if installation_id is None and not self.settings.allow_unmapped_installations:
                    # An installation belongs to whoever mapped it; guessing one from a
                    # repository name or taking the first would cross organizations.
                    logger.info("No GitHub App installation is mapped for this request")
                    return None
                # If specific repo provided, find installation for that repo
                if installation_id is None and repo and "/" in repo:
                    owner_repo = repo.strip()
                    res = await client.get(
                        f"{self.settings.github_api_url.rstrip('/')}/repos/{owner_repo}/installation",
                        headers=headers,
                    )
                    if res.status_code == 200:
                        installation_id = res.json().get("id")

                # If no specific repo or repo installation lookup failed, query all app installations
                if not installation_id:
                    res = await client.get(
                        f"{self.settings.github_api_url.rstrip('/')}/app/installations",
                        headers=headers,
                    )
                    if res.status_code == 200:
                        installations = res.json()
                        if installations and isinstance(installations, list):
                            if len(installations) == 1:
                                installation_id = installations[0].get("id")
                            elif len(installations) > 1:
                                logger.warning(
                                    "Multiple installations (%d) found for GitHub App %s; refusing ambiguous fallback",
                                    len(installations),
                                    self.settings.github_app_id,
                                )

                if not installation_id:
                    logger.warning(
                        "No installations found for GitHub App %s", self.settings.github_app_id
                    )
                    return None

                # Mint an installation access token with requested or default scopes
                token_perms = (
                    permissions
                    or self.settings.github_app_permissions
                    or {
                        "contents": "read",
                        "pull_requests": "read",
                    }
                )
                payload: dict[str, Any] = (
                    {} if inherit_permissions else {"permissions": token_perms}
                )
                if repositories:
                    # Least privilege: restrict the token to the named repositories.
                    payload["repositories"] = repositories

                token_res = await client.post(
                    f"{self.settings.github_api_url.rstrip('/')}/app/installations/{installation_id}/access_tokens",
                    headers=headers,
                    json=payload,
                )
                if token_res.status_code in (200, 201):
                    token_data = token_res.json()
                    token = str(token_data["token"]) if "token" in token_data else None
                    if token:
                        logger.info(
                            "Generated dynamic GitHub App token for installation %d with scopes: %s",
                            installation_id,
                            token_perms,
                        )
                    return token
                else:
                    logger.warning(
                        "Failed to create GitHub App installation token (%d): %s",
                        token_res.status_code,
                        token_res.text,
                    )
        except Exception as ex:
            logger.warning("Error generating dynamic GitHub App access token: %s", ex)

        return None

    async def get_installation_account(self, installation_id: int) -> str | None:
        """The account login an installation belongs to, or ``None`` if GitHub does not know it.

        Proves the installation id the browser brought back is a real installation of *this*
        App before it is mapped to an organization.
        """
        if not (self.settings.github_app_id and self.settings.github_app_private_key):
            return None
        headers = {
            "Authorization": f"Bearer {self.generate_app_jwt()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(
                f"{self.settings.github_api_url.rstrip('/')}/app/installations/{installation_id}",
                headers=headers,
            )
        if res.status_code != 200:
            # Kept in the logs: the caller only says the installation is unknown, and the reason
            # (not created yet, wrong App, bad credentials, rate limit) is GitHub's answer.
            logger.warning(
                "GitHub answered %s for installation %s: %s",
                res.status_code,
                installation_id,
                res.text[:200],
            )
            return None
        login = (res.json().get("account") or {}).get("login")
        return str(login) if login else None

    async def publish_branch(self, session_id: str, spec: PublishSpec) -> PublishResult:
        """Publish the working branch: a push, or GitHub-signed commits created through the API."""
        if not self.driver:
            raise PublishError("There is no sandbox to publish from.")

        async def mint_app_token(repository: str, installation_id: int | None) -> str | None:
            return await self.get_github_access_token(
                repo=repository,
                permissions={"contents": "write", "pull_requests": "write"},
                repositories=[repository.rsplit("/", 1)[-1]],
                installation_id=installation_id,
            )

        publisher = BranchPublisher(
            self.driver,
            self.settings.github_api_url,
            mint_app_token,
            git_base_url=self.settings.git_base_url or None,
        )
        return await publisher.publish(session_id, spec)

    async def push_and_open_pr(
        self,
        session_id: str,
        title: str,
        body: str,
        target_branch: str | None = None,
        token: str | None = None,
        installations: Sequence[tuple[int, str]] = (),
        publish: PublishSpec | None = None,
    ) -> str:
        """Push the working branch and open a pull request through the GitHub REST API.

        ``token`` (a member's own token) takes precedence over the GitHub App. When no
        credential is available, or GitHub refuses the request, this raises: a pull request
        URL is never invented.
        """
        spec = publish or PublishSpec(
            token=token, installations=tuple(installations), default_branch=target_branch or "main"
        )
        published = await self.publish_branch(session_id, spec)
        base = target_branch or published.default_branch or spec.default_branch
        owner_repo, current_branch = published.repository, published.branch

        resolved_token = token or await self.get_github_access_token(
            repo=owner_repo,
            permissions={"pull_requests": "write", "contents": "write"},
            installation_id=pick_installation(installations, owner_repo),
        )
        if not resolved_token:
            raise RuntimeError(
                "No GitHub credential is available: link a personal token or configure the GitHub App"
            )

        headers = {
            "Authorization": f"Bearer {resolved_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        api_url = f"{self.settings.github_api_url.rstrip('/')}/repos/{owner_repo}/pulls"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                api_url,
                headers=headers,
                json={
                    "title": title,
                    "body": body,
                    "head": current_branch,
                    "base": base,
                },
            )
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"GitHub refused the pull request ({resp.status_code}): {resp.text[:300]}"
            )
        html_url: str = resp.json().get("html_url", "")
        if not html_url:
            raise RuntimeError("GitHub accepted the pull request but returned no URL")
        logger.info("Opened GitHub PR: %s", html_url)
        return html_url

    async def handle_webhook(
        self,
        raw_body: bytes,
        headers: dict[str, Any],
    ) -> WebhookTrigger | None:
        """
        Verifies HMAC signature, extracts event, and returns trigger object
        if action should start or resume an agent session.
        """
        header_map = {k.lower(): v for k, v in headers.items()}
        self._verify_webhook_signature(raw_body, header_map.get("x-hub-signature-256"))

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception as err:
            logger.warning("Failed to parse webhook JSON payload: %s", err)
            return None

        repo_name = str(payload.get("repository", {}).get("full_name", ""))
        if self.settings.allowed_repos and not any(
            fnmatch.fnmatchcase(repo_name, pattern) for pattern in self.settings.allowed_repos
        ):
            logger.info("Ignoring webhook for non-allowlisted repository %s", repo_name)
            return None

        event_name = header_map.get("x-github-event", "")
        if event_name == "issues":
            return self._issue_trigger(payload)
        if event_name == "issue_comment":
            return self._issue_comment_trigger(payload)
        if event_name == "pull_request":
            return _pull_request_trigger(payload)
        return None

    def parse_repo_event(self, raw_body: bytes, headers: dict[str, Any]) -> RepoEvent | None:
        """What a verified webhook reports about a repository, whether or not it triggers a run."""
        header_map = {k.lower(): v for k, v in headers.items()}
        self._verify_webhook_signature(raw_body, header_map.get("x-hub-signature-256"))
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        repo_name = str(payload.get("repository", {}).get("full_name", ""))
        if self.settings.allowed_repos and not any(
            fnmatch.fnmatchcase(repo_name, pattern) for pattern in self.settings.allowed_repos
        ):
            return None
        parser = _EVENT_PARSERS.get(header_map.get("x-github-event", ""))
        return parser(payload) if parser else None

    def _verify_webhook_signature(self, raw_body: bytes, signature: str | None) -> None:
        """Verify the HMAC SHA-256 signature.

        Without a configured secret every webhook is rejected (fail closed) unless unsigned
        webhooks are explicitly allowed.
        """
        secret = self.settings.github_webhook_secret
        if not secret and not self.settings.allow_unsigned_webhooks:
            raise ValueError("GITHUB_WEBHOOK_SECRET is not configured; unsigned webhooks rejected")
        if not secret:
            return
        if not signature:
            raise ValueError("Missing X-Hub-Signature-256 header")
        expected = (
            "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        )
        if not hmac.compare_digest(signature, expected):
            raise ValueError("Invalid webhook HMAC signature")

    def _issue_trigger(self, payload: dict[str, Any]) -> WebhookTrigger | None:
        """``issues.labeled`` with the trigger label (``ai-fix`` by default)."""
        if payload.get("action") != "labeled":
            return None
        if payload.get("label", {}).get("name", "") != self.settings.github_trigger_label:
            return None
        issue = payload.get("issue", {})
        issue_number = issue.get("number", 0)
        title = issue.get("title", "")
        body = issue.get("body") or ""
        return _github_trigger(
            payload,
            event_type="issues.labeled",
            number=issue_number,
            branch_name=f"agent/issue-{issue_number}-fix",
            prompt_text=f"Issue #{issue_number}: {title}\n\n{body}".strip(),
        )

    def _issue_comment_trigger(self, payload: dict[str, Any]) -> WebhookTrigger | None:
        """A comment that addresses the agent, or any comment on an issue carrying the label."""
        if payload.get("action") != "created":
            return None
        comment = payload.get("comment", {})
        # Only repository insiders may steer the agent: a stranger's comment on a public
        # repo is untrusted input that must not start or resume a run.
        if comment.get("author_association") not in _TRUSTED_ASSOCIATIONS:
            return None
        comment_body = comment.get("body", "")
        issue = payload.get("issue", {})
        issue_labels = [
            label.get("name", "") for label in issue.get("labels", []) if isinstance(label, dict)
        ]
        is_addressed = any(trigger in comment_body.lower() for trigger in _COMMENT_TRIGGERS)
        if not (is_addressed or self.settings.github_trigger_label in issue_labels):
            return None
        issue_number = issue.get("number", 0)
        return _github_trigger(
            payload,
            event_type="issue_comment.created",
            number=issue_number,
            branch_name=f"agent/issue-{issue_number}-fix",
            prompt_text=comment_body,
        )


_COMMENT_TRIGGERS = ("@rocketchat", "@bot", "/agent", "/fix")


def _github_trigger(
    payload: dict[str, Any], *, event_type: str, number: int, branch_name: str, prompt_text: str
) -> WebhookTrigger:
    return WebhookTrigger(
        provider="github",
        installation_id=_installation_id(payload),
        event_type=event_type,
        repo_full_name=payload.get("repository", {}).get("full_name", "unknown/repo"),
        issue_or_pr_number=number,
        sender_login=payload.get("sender", {}).get("login", "unknown"),
        branch_name=branch_name,
        prompt_text=prompt_text,
    )


def _pull_request_trigger(payload: dict[str, Any]) -> WebhookTrigger | None:
    if payload.get("action") not in ("opened", "synchronize", "labeled"):
        return None
    pr = payload.get("pull_request", {})
    if pr.get("author_association") not in _TRUSTED_ASSOCIATIONS:
        return None
    pr_number = pr.get("number", 0)
    title = pr.get("title", "")
    body = pr.get("body") or ""
    return _github_trigger(
        payload,
        event_type="pull_request",
        number=pr_number,
        branch_name=pr.get("head", {}).get("ref", f"pr-{pr_number}"),
        prompt_text=f"Pull Request #{pr_number}: {title}\n\n{body}".strip(),
    )


def _repo_event(payload: dict[str, Any], **fields: Any) -> RepoEvent:
    return RepoEvent(
        repo_full_name=str(payload.get("repository", {}).get("full_name", "")).lower(),
        actor=str(payload.get("sender", {}).get("login", "")),
        installation_id=_installation_id(payload),
        **fields,
    )


def _pull_request_event(payload: dict[str, Any]) -> RepoEvent | None:
    if payload.get("action") != "closed":
        return None
    pr = payload.get("pull_request", {})
    merged = bool(pr.get("merged"))
    return _repo_event(
        payload,
        kind="pull_request.closed",
        summary=f"Pull request #{pr.get('number')} was {'merged' if merged else 'closed without merging'}.",
        pr_numbers=(int(pr.get("number", 0)),),
        branch=pr.get("head", {}).get("ref"),
        url=pr.get("html_url", ""),
        outcome="merged" if merged else "closed",
        ends_watch=True,
        trusted=True,
    )


def _check_suite_event(payload: dict[str, Any]) -> RepoEvent | None:
    suite = payload.get("check_suite", {})
    if payload.get("action") != "completed" or not suite.get("conclusion"):
        return None
    conclusion = str(suite["conclusion"])
    app = suite.get("app", {}).get("name", "CI")
    return _repo_event(
        payload,
        kind="check_suite.completed",
        summary=f"{app} checks finished: {conclusion} (commit {str(suite.get('head_sha', ''))[:7]}).",
        pr_numbers=tuple(int(pr["number"]) for pr in suite.get("pull_requests", [])),
        branch=suite.get("head_branch"),
        outcome=conclusion,
        trusted=True,
    )


def _review_event(payload: dict[str, Any]) -> RepoEvent | None:
    if payload.get("action") != "submitted":
        return None
    review, pr = payload.get("review", {}), payload.get("pull_request", {})
    state = str(review.get("state", "")).lower()
    reviewer = review.get("user", {}).get("login", "someone")
    return _repo_event(
        payload,
        kind="review.submitted",
        summary=f"{reviewer} reviewed pull request #{pr.get('number')}: {state}.\n{review.get('body') or ''}".strip(),
        pr_numbers=(int(pr.get("number", 0)),),
        branch=pr.get("head", {}).get("ref"),
        url=review.get("html_url", ""),
        outcome=state,
        trusted=review.get("author_association") in _TRUSTED_ASSOCIATIONS,
    )


def _comment_event(payload: dict[str, Any]) -> RepoEvent | None:
    """A comment on a pull request (an issue comment on an issue that is a pull request)."""
    if payload.get("action") != "created":
        return None
    comment = payload.get("comment", {})
    issue = payload.get("issue") or payload.get("pull_request") or {}
    if "issue" in payload and "pull_request" not in issue:
        return None
    author = comment.get("user", {}).get("login", "someone")
    return _repo_event(
        payload,
        kind="comment.created",
        summary=f"{author} commented on pull request #{issue.get('number')}:\n{comment.get('body') or ''}".strip(),
        pr_numbers=(int(issue.get("number", 0)),),
        url=comment.get("html_url", ""),
        trusted=comment.get("author_association") in _TRUSTED_ASSOCIATIONS,
    )


def _workflow_run_event(payload: dict[str, Any]) -> RepoEvent | None:
    run = payload.get("workflow_run", {})
    if payload.get("action") != "completed" or not run.get("conclusion"):
        return None
    conclusion = str(run["conclusion"])
    name = run.get("name") or "Workflow"
    head_sha = str(run.get("head_sha", ""))[:7]
    pr_numbers = tuple(int(pr["number"]) for pr in run.get("pull_requests", []) if "number" in pr)
    return _repo_event(
        payload,
        kind="workflow_run.completed",
        summary=f"{name} workflow finished: {conclusion} (commit {head_sha}).",
        pr_numbers=pr_numbers,
        branch=run.get("head_branch"),
        url=run.get("html_url", ""),
        outcome=conclusion,
        trusted=True,
    )


def _check_run_event(payload: dict[str, Any]) -> RepoEvent | None:
    run = payload.get("check_run", {})
    if payload.get("action") != "completed" or not run.get("conclusion"):
        return None
    conclusion = str(run["conclusion"])
    name = run.get("name") or "Check"
    head_sha = str(run.get("head_sha", ""))[:7]
    prs = run.get("pull_requests") or (run.get("check_suite") or {}).get("pull_requests", [])
    pr_numbers = tuple(int(pr["number"]) for pr in prs if "number" in pr)
    branch = (run.get("check_suite") or {}).get("head_branch")
    return _repo_event(
        payload,
        kind="check_run.completed",
        summary=f"{name} check finished: {conclusion} (commit {head_sha}).",
        pr_numbers=pr_numbers,
        branch=branch,
        url=run.get("html_url", ""),
        outcome=conclusion,
        trusted=True,
    )


_EVENT_PARSERS = {
    "pull_request": _pull_request_event,
    "check_suite": _check_suite_event,
    "workflow_run": _workflow_run_event,
    "check_run": _check_run_event,
    "pull_request_review": _review_event,
    "issue_comment": _comment_event,
    "pull_request_review_comment": _comment_event,
}
