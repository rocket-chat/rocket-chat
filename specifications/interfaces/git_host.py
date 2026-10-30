"""A git forge the platform works with: GitHub, GitLab (including self-hosted), more later.

``git-engine`` implements one ``GitHostProtocol`` per forge. The platform picks the forge of a
repository from its remote URL (``owns``), so publishing, opening a change request (a GitHub pull
request, a GitLab merge request), reading CI state and reading webhooks go through one contract;
the sandbox still never holds a credential (it clones through the platform's git gateway and
publishes through the platform).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from specifications.interfaces.automation import TriggerEvent
from specifications.interfaces.git import RepoEvent

HOST_KINDS: tuple[str, ...] = ("github", "gitlab")


@dataclass(frozen=True)
class CiState:
    """The combined CI state of a commit: ``pending``, ``success``, ``failure`` or ``none``."""

    state: str
    url: str = ""
    summary: str = ""


@dataclass(frozen=True)
class ChangeRequest:
    """A pull request (GitHub) or merge request (GitLab) the platform opened."""

    repository: str
    number: int
    url: str


class GitHostProtocol(Protocol):
    kind: str
    # The forge's web address (``https://github.com``, ``https://gitlab.example.com``).
    web_url: str
    # The user name a token is sent with over git's HTTP Basic authentication.
    basic_user: str

    def owns(self, remote_url: str) -> bool:
        """Whether a repository's ``origin`` URL is on this forge."""
        ...

    def repository_of(self, remote_url: str) -> str:
        """``owner/name`` (GitLab: ``group/subgroup/name``) of a remote on this forge."""
        ...

    def verify_webhook(self, raw_body: bytes, headers: dict[str, str]) -> None:
        """Raise ``ValueError`` unless the webhook is authentic (fails closed without a secret)."""
        ...

    def trigger_event(self, raw_body: bytes, headers: dict[str, str]) -> TriggerEvent | None: ...

    def repo_event(self, raw_body: bytes, headers: dict[str, str]) -> RepoEvent | None: ...

    async def open_change_request(
        self, repository: str, *, head: str, base: str, title: str, body: str, token: str
    ) -> ChangeRequest: ...

    async def ci_state(self, repository: str, sha: str, token: str) -> CiState: ...
