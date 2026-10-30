"""GitHub as a ``GitHostProtocol``, over the existing engine: the contract's other forges (GitLab)
are held to the same shape."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from git_engine.config import github_web_url
from git_engine.engine import GitEngine
from git_engine.publish import PublishError, repository_of
from specifications.interfaces.automation import TriggerEvent
from specifications.interfaces.git import RepoEvent
from specifications.interfaces.git_host import ChangeRequest, CiState

GITHUB_TIMEOUT_SECONDS = 15.0
_STATES = {"success": "success", "failure": "failure", "error": "failure", "pending": "pending"}


class GitHubHost:
    kind = "github"
    basic_user = "x-access-token"

    def __init__(self, engine: GitEngine) -> None:
        self._engine = engine
        self.api_url = engine.settings.github_api_url.rstrip("/")
        self.web_url = github_web_url(self.api_url)
        self._host = (urlparse(self.web_url).hostname or "github.com").lower()

    def owns(self, remote_url: str) -> bool:
        text = remote_url.strip()
        scp = re.match(r"^git@([^:/]+):", text)
        host = scp.group(1) if scp else urlparse(text).hostname or ""
        return host.lower() == self._host

    def repository_of(self, remote_url: str) -> str:
        return repository_of(remote_url, self._host)

    def verify_webhook(self, raw_body: bytes, headers: dict[str, str]) -> None:
        lowered = {k.lower(): v for k, v in headers.items()}
        self._engine._verify_webhook_signature(raw_body, lowered.get("x-hub-signature-256"))

    def trigger_event(self, raw_body: bytes, headers: dict[str, str]) -> TriggerEvent | None:
        return self._engine.trigger_event(raw_body, headers)

    def repo_event(self, raw_body: bytes, headers: dict[str, str]) -> RepoEvent | None:
        return self._engine.parse_repo_event(raw_body, headers)

    async def _request(
        self, method: str, path: str, token: str, body: dict[str, object] | None = None
    ) -> dict[str, object]:
        async with httpx.AsyncClient(timeout=GITHUB_TIMEOUT_SECONDS) as client:
            response = await client.request(
                method,
                f"{self.api_url}{path}",
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        if response.status_code >= 400:
            raise PublishError(f"GitHub answered {response.status_code}: {response.text[:300]}")
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def open_change_request(
        self, repository: str, *, head: str, base: str, title: str, body: str, token: str
    ) -> ChangeRequest:
        made = await self._request(
            "POST",
            f"/repos/{repository}/pulls",
            token,
            {"title": title, "body": body, "head": head, "base": base},
        )
        return ChangeRequest(
            repository, int(str(made.get("number") or 0)), str(made.get("html_url") or "")
        )

    async def ci_state(self, repository: str, sha: str, token: str) -> CiState:
        combined = await self._request("GET", f"/repos/{repository}/commits/{sha}/status", token)
        state = str(combined.get("state") or "")
        if not combined.get("total_count"):
            return CiState("none")
        return CiState(_STATES.get(state, "pending"), summary=f"Combined status {state}")
