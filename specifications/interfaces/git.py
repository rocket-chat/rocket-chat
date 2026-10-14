"""
Interface definitions for Git Operations & GitHub Webhook Ingress.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SigningMode(str, Enum):
    GITHUB_APP = "github_app"
    GPG_KEY = "gpg_key"
    SSH_KEY = "ssh_key"
    NONE = "none"


@dataclass
class CoAuthor:
    name: str
    email: str


@dataclass
class CommitSpec:
    message: str
    # Branch to commit on; empty means "the branch that is currently checked out".
    branch: str = ""
    co_authors: list[CoAuthor] = field(default_factory=list)
    signing_mode: SigningMode = SigningMode.GITHUB_APP
    gpg_key_id: str | None = None
    # Identity recorded as author/committer; None falls back to the engine's bot identity.
    committer_name: str | None = None
    committer_email: str | None = None


@dataclass
class PublishSpec:
    """How a branch leaves the sandbox: the one path that holds write credentials.

    ``GITHUB_APP`` replays the branch's new commits through GitHub's API so GitHub signs them
    (verified as the App, or as the member when ``token`` is the member's own); any other mode
    pushes the commits the sandbox made, which are already signed (or not) locally.
    """

    # Empty means "the branch that is currently checked out".
    branch: str = ""
    signing_mode: SigningMode = SigningMode.NONE
    # The member's own write token; the GitHub App's is minted when this is None.
    token: str | None = None
    installations: tuple[tuple[int, str], ...] = ()
    default_branch: str = "main"
    force_with_lease: bool = False


@dataclass
class PublishResult:
    repository: str
    branch: str
    head_sha: str
    via: str  # "github_api" | "git_push"
    commits: int = 0
    # Things the member should know about what was published, e.g. a lost executable bit.
    notices: list[str] = field(default_factory=list)


@dataclass
class WebhookTrigger:
    provider: str  # "github"
    event_type: str  # "issues.labeled", "issue_comment.created", "pull_request"
    repo_full_name: str
    issue_or_pr_number: int
    sender_login: str
    branch_name: str | None = None
    prompt_text: str = ""
    # The GitHub App installation the event came through; maps the event to an organization.
    installation_id: int | None = None


@dataclass(frozen=True)
class RepoEvent:
    """Something that happened on a repository that a session following it may care about."""

    kind: (
        str  # "pull_request.closed", "check_suite.completed", "review.submitted", "comment.created"
    )
    repo_full_name: str
    summary: str
    pr_numbers: tuple[int, ...] = ()
    branch: str | None = None
    actor: str = ""
    url: str = ""
    # CI outcome ("success", "failure", ...) or review state, when the event has one.
    outcome: str = ""
    # The pull request will not change any more (merged or closed).
    ends_watch: bool = False
    # The author is a repository insider; only their events may start a turn on their own.
    trusted: bool = False
    installation_id: int | None = None


class GitEngineProtocol(ABC):
    """Contract for Git operations and automated GitHub session management."""

    @abstractmethod
    async def create_commit(
        self,
        session_id: str,
        spec: CommitSpec,
    ) -> str:
        """
        Formats commit message with Co-authored-by trailers, executes signing,
        and creates commit. Returns commit SHA.
        """
        pass

    @abstractmethod
    async def publish_branch(self, session_id: str, spec: PublishSpec) -> PublishResult:
        """Pushes the working branch to GitHub, signed by GitHub when ``spec`` asks for it.

        Raises with the reason when the branch cannot be published; never reports a
        publication that did not happen.
        """
        pass

    @abstractmethod
    async def push_and_open_pr(
        self,
        session_id: str,
        title: str,
        body: str,
        target_branch: str = "main",
        token: str | None = None,
        installations: Sequence[tuple[int, str]] = (),
        publish: PublishSpec | None = None,
    ) -> str:
        """Pushes the working branch and opens a Pull Request, returning its URL.

        ``token`` (a member's own GitHub token) is used instead of the GitHub App when given.
        Raises when no credential is available or GitHub refuses: a pull request link is
        never fabricated.
        """
        pass

    @abstractmethod
    async def handle_webhook(
        self,
        raw_body: bytes,
        headers: dict[str, Any],
    ) -> WebhookTrigger | None:
        """
        Verifies HMAC signature, extracts event, and returns trigger object
        if action should start or resume an agent session.
        """
        pass

    @abstractmethod
    def parse_repo_event(self, raw_body: bytes, headers: dict[str, Any]) -> RepoEvent | None:
        """The repository event a verified webhook reports, for sessions following that repository."""
        pass
