"""
Interface definitions for Git Operations & GitHub Webhook Ingress.
"""

from abc import ABC, abstractmethod
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
    branch: str
    co_authors: list[CoAuthor] = field(default_factory=list)
    signing_mode: SigningMode = SigningMode.GITHUB_APP
    gpg_key_id: str | None = None


@dataclass
class WebhookTrigger:
    provider: str  # "github"
    event_type: str  # "issues.labeled", "issue_comment.created", "pull_request"
    repo_full_name: str
    issue_or_pr_number: int
    sender_login: str
    branch_name: str | None = None
    prompt_text: str = ""


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
    async def push_and_open_pr(
        self,
        session_id: str,
        title: str,
        body: str,
        target_branch: str = "main",
    ) -> str:
        """Pushes working branch and opens a Pull Request. Returns PR URL."""
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
