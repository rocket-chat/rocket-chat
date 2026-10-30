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


@dataclass(frozen=True)
class SigningKey:
    """A member's GPG key the platform signs their published commits with.

    It is used on the platform only, in a throwaway keyring, when ``git_push`` publishes: it is
    never given to a sandbox.
    """

    fingerprint: str
    private_key: str = field(repr=False)
    passphrase: str = field(default="", repr=False)


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
    # Author identity when distinct from committer; None falls back to committer.
    author_name: str | None = None
    author_email: str | None = None
    # The repository folder, absolute or relative to /workspace. Empty: the session's active
    # repository, /workspace itself, or the one repository found under it.
    path: str = ""


@dataclass
class PublishSpec:
    """How a branch leaves the sandbox: the one path that holds write credentials.

    The sandbox hands over a bundle of the branch's new commits; everything else happens on the
    platform. ``GITHUB_APP`` replays them through GitHub's API so GitHub signs them (verified as
    the App, or as the member when ``token`` is the member's own); ``GPG_KEY`` with a
    ``signing_key`` re-creates them signed with it; any other mode pushes them as they are.
    """

    # Empty means "the branch that is currently checked out".
    branch: str = ""
    signing_mode: SigningMode = SigningMode.NONE
    # The member's own write token; the GitHub App's is minted when this is None.
    token: str | None = None
    # Used only when no GitHub App token can be minted (no installation is mapped to the
    # organization): the member's own token, so their access to the repository is enough.
    fallback_token: str | None = None
    # The repository folder, absolute or relative to /workspace. Empty: /workspace itself, or
    # the one repository found under it.
    path: str = ""
    installations: tuple[tuple[int, str], ...] = ()
    default_branch: str = "main"
    force_with_lease: bool = False
    signing_key: SigningKey | None = field(default=None, repr=False)
    # Credited on every commit GitHub re-creates (``GITHUB_APP``), whatever made it: the
    # ``git_commit`` tool or a plain ``git commit`` in the shell.
    co_authors: tuple[CoAuthor, ...] = ()
    # The member's GitLab token, used when the repository is on a GitLab forge (else the
    # deployment's ``GITLAB_TOKEN``). GitLab has no App: there is no signed replay there.
    gitlab_token: str | None = field(default=None, repr=False)


@dataclass
class PublishResult:
    repository: str
    branch: str
    head_sha: str
    via: str  # "github_api" | "git_push"
    commits: int = 0
    # Things the member should know about what was published, e.g. a lost executable bit.
    notices: list[str] = field(default_factory=list)
    # (local, published) hashes of commits re-created on publication (signed by GitHub or with
    # the member's key, or credited to them); the sandbox's branch then points at the published
    # ones. Empty when the commits were pushed as they are.
    rewritten: list[tuple[str, str]] = field(default_factory=list)
    # The repository's own default branch (from its clone), else the setting's: where a pull
    # request goes unless the caller names another base.
    default_branch: str = ""
    # The forge it was published to ("github" or "gitlab"): where its change request opens.
    forge: str = "github"


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
    # The commit a CI event is about: a run for an older commit must not wake a session that
    # has pushed since.
    head_sha: str = ""


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
        target_branch: str | None = None,
        token: str | None = None,
        installations: Sequence[tuple[int, str]] = (),
        publish: PublishSpec | None = None,
    ) -> str:
        """Pushes the working branch and opens a Pull Request, returning its URL.

        Without ``target_branch`` the pull request goes to the repository's own default branch.

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
