"""Git Engine implementing GitEngineProtocol with Co-Authoring, Signing, and Webhooks."""

import fnmatch
import hashlib
import hmac
import json
import logging
import re
import shlex
import time
from collections.abc import Sequence
from typing import Any

import httpx
import jwt

from git_engine.config import GitEngineSettings
from git_engine.trailers import format_commit_message
from specifications.interfaces.git import (
    CommitSpec,
    GitEngineProtocol,
    RepoEvent,
    SigningMode,
    WebhookTrigger,
)
from specifications.interfaces.sandbox import SandboxDriverProtocol

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

        # 1. Ensure git binary is available inside the execution container
        check_git = await self.driver.exec_command(session_id, "which git")
        if check_git.exit_code != 0:
            await self.driver.exec_command(
                session_id,
                "apt-get update -y && apt-get install -y --no-install-recommends git || apk add --no-cache git || true",
            )

        # 2. Ensure git repository is initialized
        check_repo = await self.driver.exec_command(
            session_id, "git rev-parse --is-inside-work-tree"
        )
        if check_repo.exit_code != 0:
            init_cmd = f"git init -b {shlex.quote(spec.branch or self.settings.default_branch)}"
            await self.driver.exec_command(session_id, init_cmd)

        # 3. Configure the committer identity: the caller's (policy-resolved) identity, else
        # this deployment's own bot. Never another product's identity.
        author_name = spec.committer_name or self.settings.bot_author_name
        author_email = spec.committer_email or self.settings.bot_author_email

        await self.driver.exec_command(
            session_id,
            f"git config user.name {shlex.quote(author_name)} && "
            f"git config user.email {shlex.quote(author_email)}",
        )

        # 3. Checkout target branch
        if spec.branch:
            await self.driver.exec_command(
                session_id, f"git checkout -B {shlex.quote(spec.branch)}"
            )

        # 4. Stage all modified and new files
        await self.driver.exec_command(session_id, "git add -A")

        # 5. Handle Cryptographic Signing Mode
        if spec.signing_mode == SigningMode.GPG_KEY:
            if spec.gpg_key_id:
                await self.driver.exec_command(
                    session_id,
                    f"git config user.signingkey {shlex.quote(spec.gpg_key_id)}",
                )
            await self.driver.exec_command(session_id, "git config commit.gpgsign true")
            commit_cmd = f"git commit -S -m {shlex.quote(formatted_message)}"
        else:
            commit_cmd = f"git commit -m {shlex.quote(formatted_message)}"

        res = await self.driver.exec_command(session_id, commit_cmd)
        if res.exit_code != 0:
            # Check if there was nothing to commit
            if "nothing to commit" in res.stdout or "nothing to commit" in res.stderr:
                rev_res = await self.driver.exec_command(session_id, "git rev-parse HEAD")
                return rev_res.stdout.strip()
            raise RuntimeError(f"git commit failed (exit {res.exit_code}): {res.stderr}")

        # 6. Retrieve committed SHA
        sha_res = await self.driver.exec_command(session_id, "git rev-parse HEAD")
        return sha_res.stdout.strip()

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
    ) -> str | None:
        """
        Retrieves a valid GitHub access token.
        1. Returns user/PAT token if explicitly set (GITHUB_TOKEN / GITHUB_PAT).
        2. Otherwise, if GitHub App credentials are configured (GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY),
           dynamically creates a scoped installation access token.
        """
        # If explicit user token is provided, prioritize it
        if self.settings.github_token:
            return self.settings.github_token

        # Fallback to dynamic GitHub App installation token
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
                            installation_id = installations[0].get("id")

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
                payload: dict[str, Any] = {"permissions": token_perms}
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
            return None
        login = (res.json().get("account") or {}).get("login")
        return str(login) if login else None

    async def push_and_open_pr(
        self,
        session_id: str,
        title: str,
        body: str,
        target_branch: str = "main",
        token: str | None = None,
        installations: Sequence[tuple[int, str]] = (),
    ) -> str:
        """Push the working branch and open a pull request through the GitHub REST API.

        ``token`` (a member's own token) takes precedence over the GitHub App. When no
        credential is available, or GitHub refuses the request, this raises: a pull request
        URL is never invented.
        """
        current_branch = "agent-fix"
        remote_url = ""
        if self.driver:
            branch_res = await self.driver.exec_command(session_id, "git branch --show-current")
            if branch_res.exit_code == 0 and branch_res.stdout.strip():
                current_branch = branch_res.stdout.strip()

            remote_res = await self.driver.exec_command(
                session_id, "git config --get remote.origin.url"
            )
            if remote_res.exit_code == 0 and remote_res.stdout.strip():
                remote_url = remote_res.stdout.strip()

            push = await self.driver.exec_command(
                session_id, f"git push -u origin {shlex.quote(current_branch)}"
            )
            if push.exit_code != 0:
                raise RuntimeError(f"git push failed: {(push.stderr or push.stdout).strip()}")

        match = re.search(r"github\.com[:/]([^/]+)/([^/\.]+)", remote_url)
        if match is None:
            raise RuntimeError(
                "The workspace has no GitHub 'origin' remote to open a pull request on"
            )
        owner_repo = f"{match.group(1)}/{match.group(2)}"

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
                    "base": target_branch,
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


_EVENT_PARSERS = {
    "pull_request": _pull_request_event,
    "check_suite": _check_suite_event,
    "pull_request_review": _review_event,
    "issue_comment": _comment_event,
    "pull_request_review_comment": _comment_event,
}
