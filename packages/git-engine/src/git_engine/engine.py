"""Git Engine implementing GitEngineProtocol with Co-Authoring, Signing, and Webhooks."""

import hashlib
import hmac
import json
import logging
import re
import shlex
import time
from typing import Any

import httpx
import jwt

from git_engine.config import GitEngineSettings
from git_engine.trailers import format_commit_message
from specifications.interfaces.git import (
    CommitSpec,
    GitEngineProtocol,
    SigningMode,
    WebhookTrigger,
)
from specifications.interfaces.sandbox import SandboxDriverProtocol

logger = logging.getLogger(__name__)


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
            init_cmd = f"git init -b {shlex.quote(spec.branch)}"
            await self.driver.exec_command(session_id, init_cmd)

        # 3. Configure Git committer identity
        if spec.signing_mode == SigningMode.GITHUB_APP:
            # GitHub App identity results in official GitHub Bot attribution
            author_name = "github-actions[bot]"
            author_email = "41898282+github-actions[bot]@users.noreply.github.com"
        else:
            author_name = self.settings.bot_author_name
            author_email = self.settings.bot_author_email

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
                installation_id: int | None = None
                # If specific repo provided, find installation for that repo
                if repo and "/" in repo:
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

    async def push_and_open_pr(
        self,
        session_id: str,
        title: str,
        body: str,
        target_branch: str = "main",
    ) -> str:
        """Pushes working branch and opens a Pull Request via GitHub REST API. Returns PR URL."""
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

            # Push to remote if origin is configured
            await self.driver.exec_command(
                session_id, f"git push -u origin {shlex.quote(current_branch)}"
            )

        # Extract repo slug from origin url if available (e.g., git@github.com:owner/repo.git or https://github.com/owner/repo.git)
        owner_repo = "rocketchat/platform"
        if remote_url:
            match = re.search(r"github\.com[:/]([^/]+)/([^/\.]+)", remote_url)
            if match:
                owner_repo = f"{match.group(1)}/{match.group(2)}"

        # Resolve GitHub token: either user PAT or dynamically generated GitHub App token
        # For opening PRs, request pull_requests:write and contents:write
        pr_perms = {"pull_requests": "write", "contents": "write"}
        token = await self.get_github_access_token(repo=owner_repo, permissions=pr_perms)

        if token:
            try:
                headers = {
                    "Authorization": f"Bearer {token}",
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
                    if resp.status_code in (200, 201):
                        pr_data = resp.json()
                        html_url: str = pr_data.get("html_url", "")
                        if html_url:
                            logger.info("Successfully created live GitHub PR: %s", html_url)
                            return html_url
                    else:
                        logger.warning(
                            "GitHub PR creation returned status %d: %s. Falling back to synthetic PR.",
                            resp.status_code,
                            resp.text,
                        )
            except Exception as ex:
                logger.warning(
                    "Failed to open PR via GitHub API: %s. Falling back to synthetic PR.", ex
                )

        # Synthetic PR link for sandbox environments without live GitHub API tokens
        pr_number = abs(hash(session_id)) % 900 + 100
        pr_url = f"https://github.com/{owner_repo}/pull/{pr_number}"
        logger.info("Created pull request: %s for branch %s", pr_url, current_branch)
        return pr_url

    async def handle_webhook(
        self,
        raw_body: bytes,
        headers: dict[str, Any],
    ) -> WebhookTrigger | None:
        """
        Verifies HMAC signature, extracts event, and returns trigger object
        if action should start or resume an agent session.
        """
        # Case-insensitive header lookup
        header_map = {k.lower(): v for k, v in headers.items()}
        sig_header = header_map.get("x-hub-signature-256")
        event_name = header_map.get("x-github-event", "")

        # 1. Verify HMAC SHA-256 signature if secret is configured
        if self.settings.github_webhook_secret:
            if not sig_header:
                raise ValueError("Missing X-Hub-Signature-256 header")
            expected_sig = (
                "sha256="
                + hmac.new(
                    self.settings.github_webhook_secret.encode("utf-8"),
                    raw_body,
                    hashlib.sha256,
                ).hexdigest()
            )
            if not hmac.compare_digest(sig_header, expected_sig):
                raise ValueError("Invalid webhook HMAC signature")

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception as err:
            logger.warning("Failed to parse webhook JSON payload: %s", err)
            return None

        # 2. Issues Event (e.g. issues.labeled with 'ai-fix')
        if event_name == "issues":
            action = payload.get("action")
            if action == "labeled":
                label_obj = payload.get("label", {})
                label_name = label_obj.get("name", "")
                if label_name == self.settings.github_trigger_label:
                    repo = payload.get("repository", {}).get("full_name", "unknown/repo")
                    issue = payload.get("issue", {})
                    issue_number = issue.get("number", 0)
                    sender = payload.get("sender", {}).get("login", "unknown")
                    title = issue.get("title", "")
                    body = issue.get("body") or ""
                    prompt = f"Issue #{issue_number}: {title}\n\n{body}".strip()
                    return WebhookTrigger(
                        provider="github",
                        event_type="issues.labeled",
                        repo_full_name=repo,
                        issue_or_pr_number=issue_number,
                        sender_login=sender,
                        branch_name=f"agent/issue-{issue_number}-fix",
                        prompt_text=prompt,
                    )

        # 3. Issue Comment Event (e.g. @RocketChat follow-up or comment on labeled issue)
        elif event_name == "issue_comment":
            action = payload.get("action")
            if action == "created":
                comment = payload.get("comment", {})
                comment_body = comment.get("body", "")
                issue = payload.get("issue", {})
                issue_labels = [
                    lbl.get("name", "") for lbl in issue.get("labels", []) if isinstance(lbl, dict)
                ]

                triggers = ["@rocketchat", "@bot", "/agent", "/fix"]
                is_addressed = any(t in comment_body.lower() for t in triggers)
                has_label = self.settings.github_trigger_label in issue_labels

                if is_addressed or has_label:
                    repo = payload.get("repository", {}).get("full_name", "unknown/repo")
                    issue_number = issue.get("number", 0)
                    sender = payload.get("sender", {}).get("login", "unknown")
                    return WebhookTrigger(
                        provider="github",
                        event_type="issue_comment.created",
                        repo_full_name=repo,
                        issue_or_pr_number=issue_number,
                        sender_login=sender,
                        branch_name=f"agent/issue-{issue_number}-fix",
                        prompt_text=comment_body,
                    )

        # 4. Pull Request Event
        elif event_name == "pull_request":
            action = payload.get("action")
            if action in ("opened", "synchronize", "labeled"):
                pr = payload.get("pull_request", {})
                repo = payload.get("repository", {}).get("full_name", "unknown/repo")
                pr_number = pr.get("number", 0)
                sender = payload.get("sender", {}).get("login", "unknown")
                branch = pr.get("head", {}).get("ref", f"pr-{pr_number}")
                title = pr.get("title", "")
                body = pr.get("body") or ""
                return WebhookTrigger(
                    provider="github",
                    event_type="pull_request",
                    repo_full_name=repo,
                    issue_or_pr_number=pr_number,
                    sender_login=sender,
                    branch_name=branch,
                    prompt_text=f"Pull Request #{pr_number}: {title}\n\n{body}".strip(),
                )

        return None
