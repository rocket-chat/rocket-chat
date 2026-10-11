"""
Interface definitions for Agent Workflow & Sub-Agent Orchestration.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from specifications.interfaces.llm import AttachmentRef, ModelRouterProtocol, ToolDefinition


class SubAgentRole(str, Enum):
    PLANNER = "planner"
    RESEARCHER = "researcher"
    CODER = "coder"
    TESTER = "tester"
    REVIEWER = "reviewer"
    SECURITY_AUDITOR = "security_auditor"
    QA_VERIFIER = "qa_verifier"
    GENERAL = "general"


@dataclass
class SubagentConfig:
    """Configurable behavioral definition for a specialized subagent."""

    id: str
    name: str
    role_title: str
    description: str
    system_prompt: str
    model: str | None = None
    max_turns: int = 5
    whitelisted_tools: list[str] = field(default_factory=lambda: ["*"])
    temperature: float = 0.2
    can_ask_user: bool = False
    can_delegate: bool = False
    enabled: bool = True


@dataclass
class SubagentTaskRequest:
    """Request payload to delegate a task to a subagent."""

    role: str
    task: str
    context: str = ""
    subagent_id: str | None = None


@dataclass
class SubagentTaskResult:
    """Structured response returned by a subagent upon completing its delegated mission."""

    role: str
    task: str
    summary: str
    status: str = "completed"  # "completed", "failed", "timeout"
    turns_taken: int = 1
    tool_calls_count: int = 0
    duration_ms: int = 0
    subagent_id: str = ""


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class PlanTask:
    id: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    assigned_subagent: SubAgentRole = SubAgentRole.CODER


@dataclass
class PlanChecklist:
    session_id: str
    tasks: list[PlanTask] = field(default_factory=list)


@dataclass
class ApprovalRequest:
    action_id: str
    action_type: str  # "bash_exec", "file_delete", "git_push"
    command_or_path: str
    risk_level: str  # "low", "medium", "critical"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class InteractiveQuestion:
    question_id: str
    question_text: str
    options: list[str]
    is_multi_select: bool = False
    default_recommended_option: str | None = None


# Rendered between the cacheable prefix and the volatile suffix of a system prompt.
DYNAMIC_SECTION_HEADER = "\n\n# --- Session Context (volatile; not cacheable) ---\n"


class ClusterRelayProtocol(Protocol):
    """Reaches the API replica that holds a turn's pending gate or running task.

    A decision (approval, answer, cancel) can arrive at a different replica than the one
    running the turn. The orchestrator tries its own state first and falls back to this relay.
    """

    async def forward_approval(self, session_id: str, action_id: str, approved: bool) -> bool:
        """Resolve an approval held by another replica; ``False`` if none holds it."""
        ...

    async def forward_question(
        self,
        session_id: str,
        question_id: str,
        selected_options: list[str],
        custom_text: str | None,
    ) -> bool:
        """Answer a question held by another replica; ``False`` if none holds it."""
        ...

    async def broadcast_cancel(self, session_id: str) -> None:
        """Ask every other replica to halt its turn for the session."""
        ...


class ExternalToolProvider(Protocol):
    """Tools a turn borrows from outside the registry (e.g. the caller's MCP servers).

    Definitions are visible to - and callable by - only the turn that carries the provider, so
    one tenant's connectors can never appear in another tenant's turn.
    """

    def definitions(self) -> list[ToolDefinition]:
        """Tool schemas to advertise to the model this turn."""
        ...

    def owns(self, name: str) -> bool:
        """Whether ``name`` is one of this provider's tools."""
        ...

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute the tool and return its text result (errors are returned, not raised)."""
        ...


@dataclass(frozen=True)
class GithubAccess:
    """How a turn's read-only GitHub tools reach the API: the credential and where it points.

    ``token`` is the member's linked account or the organization's App installation token
    (``None`` means only public data is reachable). It is held by the tool code and never
    handed to the model.
    """

    token: str | None
    api_url: str = "https://api.github.com"


@dataclass
class TurnCallbacks:
    """Collaborators one turn's tools need, scoped to that turn (never shared state).

    Tools resolve these from the turn's execution context. Binding them on a shared
    registry would let two concurrent turns overwrite each other's approvals, questions
    and sandbox provisioning.
    """

    ensure_sandbox: Callable[[str], Awaitable[None]] | None = None
    rename: Callable[[str, str], Awaitable[None]] | None = None
    kb_search: Callable[[str, str | None, int], Awaitable[str]] | None = None
    execute_skill: Callable[[str, dict[str, Any]], Awaitable[str]] | None = None
    question: Callable[[str, list[str], bool, str | None], Awaitable[str]] | None = None
    approval: Callable[[str, str, str], Awaitable[str]] | None = None
    diff: Callable[[FileDiff], None] | None = None
    plan: Callable[[list[dict[str, Any]]], None] | None = None
    subagent: Callable[[str, str, str], Awaitable[str]] | None = None
    external_tools: ExternalToolProvider | None = None
    # Credential for the read-only GitHub tools (find repositories, read files, issues).
    github_access: Callable[[], Awaitable[GithubAccess]] | None = None
    # Called with (session id, folder) after the agent cloned a repository; returns a note for
    # the agent (for example the outcome of the repository's own setup script) or None.
    repository_cloned: Callable[[str, str], Awaitable[str | None]] | None = None
    # Called after a background process tool ran so the tracker can mirror the sandbox's
    # processes into the database and announce changes.
    processes_changed: Callable[[str], Awaitable[None]] | None = None
    # How many background processes the session may run at once.
    max_background_processes: int = 5
    # The organization's subagent roles for this turn (None = the built-in defaults).
    subagent_configs: Mapping[str, SubagentConfig] | None = None


@dataclass(frozen=True)
class SystemPrompt:
    """A system prompt split into a cacheable prefix and a volatile suffix.

    Prompt caching only pays off when the request prefix is byte-stable across
    turns. ``stable`` MUST therefore be byte-identical for the whole session (no
    timestamps, UUIDs, or ordering-dependent rendering); ``dynamic`` may change
    every turn — it is appended after the cache boundary and never cached.
    """

    stable: str = ""
    dynamic: str = ""

    def render(self) -> str:
        """Render the full prompt with the stable prefix first."""
        if not self.dynamic:
            return self.stable
        if not self.stable:
            return self.dynamic
        return f"{self.stable}{DYNAMIC_SECTION_HEADER}{self.dynamic}"

    @classmethod
    def from_text(cls, text: str | None) -> SystemPrompt:
        """Treat a single opaque prompt as fully stable (legacy callers)."""
        return cls(stable=text or "", dynamic="")

    @property
    def cache_prefix(self) -> str:
        """The byte-stable portion eligible for provider prompt caching."""
        return self.stable


@dataclass(frozen=True)
class TurnOverlay:
    """Transport-specific additions to a shared turn (e.g. Slack formatting rules).

    Transports (Slack, webhooks) run the *same* turn pipeline as the web cockpit - tracing,
    audit, redaction, approvals, governance - and only layer this on top: extra system-prompt
    text, extra toolsets, and a final say on which tools the caller may use.
    """

    stable_suffix: str = ""
    dynamic: str = ""
    enabled_toolsets: frozenset[str] = frozenset()
    # Files the transport already ingested for this turn (e.g. Slack uploads); they reach the
    # model as vision/text blocks through the same path as web attachments.
    attachments: tuple[AttachmentRef, ...] = ()
    # Agent persona used when the session is not explicitly bound to one.
    default_agent_id: str | None = None
    # Receives the bound agent's tool whitelist (None = unrestricted) and returns the
    # whitelist to enforce for this caller.
    tool_scope: Callable[[list[str] | None], list[str] | None] | None = None


@dataclass(frozen=True)
class GenerationParams:
    """Sampling and provider parameters for a turn's model calls (None = provider default)."""

    temperature: float | None = None
    max_tokens: int | None = None
    thinking_budget_tokens: int | None = None
    reasoning_effort: str | None = None
    fallback_models: tuple[str, ...] = ()


@dataclass(frozen=True)
class GitPolicy:
    """Organization git policy plus the member's identity, resolved once per turn.

    ``authorship``: ``co_authored`` commits as the bot with the member as a
    ``Co-authored-by`` trailer, ``user_only`` commits as the member, ``bot_only`` commits as
    the bot. ``pr_creation`` ``as_user`` opens pull requests with the member's own token when
    one is linked, ``as_bot`` always uses the GitHub App.
    """

    authorship: str = "co_authored"
    pr_creation: str = "as_user"
    signing_mode: str = "github_app"
    default_branch: str = "main"
    bot_name: str = "RocketChat Bot"
    bot_email: str = "bot@rocketchat.internal"
    user_name: str = ""
    user_email: str = ""
    user_token: str | None = field(default=None, repr=False)
    # (installation id, account login) pairs the organization mapped; the only GitHub App
    # installations a bot-authored push or pull request may use.
    app_installations: tuple[tuple[int, str], ...] = ()


# Prefix of the text a granted approval returns to the tool that was waiting for it.
APPROVAL_GRANTED_PREFIX = "Approved by the user"
HOOK_EVENTS: tuple[str, ...] = ("pre_tool", "post_tool")


@dataclass(frozen=True)
class HookSpec:
    """An organization command run in the sandbox around tool calls.

    ``pre_tool`` runs before a matching tool and a non-zero exit blocks the call;
    ``post_tool`` runs afterwards and a failure is reported back to the agent.
    """

    event: str
    match: str  # fnmatch pattern on the tool name
    command: str
    timeout_seconds: int = 30


PERMISSION_MODES: tuple[str, ...] = ("plan", "ask", "auto_edit", "auto", "bypass")
DEFAULT_PERMISSION_MODE = "auto"


@dataclass(frozen=True)
class TurnPolicy:
    """Organization/agent policy applied to one turn (carried on the execution context)."""

    generation: GenerationParams = field(default_factory=GenerationParams)
    git: GitPolicy = field(default_factory=GitPolicy)
    # How much the human supervises this turn: ``plan`` is read-only, ``ask`` gates every edit,
    # command and publish, ``auto_edit`` gates commands and publishing, ``auto`` leaves gating to
    # the agent's own ``request_approval``, ``bypass`` approves everything the org does not deny.
    permission_mode: str = DEFAULT_PERMISSION_MODE
    hooks: tuple[HookSpec, ...] = ()
    # What the session's sandbox image declares it can do (for example "browser"). ``None``
    # means unknown and nothing is hidden; a set hides tools whose capability is missing.
    sandbox_capabilities: frozenset[str] | None = None


@dataclass(frozen=True)
class AgentBinding:
    """Transport-agnostic description of how a turn should run for a session.

    Produced by the shared ``TurnService`` and consumed by every transport (web, Slack):
    the resolved agent persona, effective model, turn limit, tool scope and toolset.
    ``None`` tool whitelist means "no restriction from the agent"; ``model_router`` is the
    per-role router (opaque here so this interface stays free of app/config types).
    """

    id: str
    name: str = ""
    system_prompt: str | None = None
    whitelisted_tools: list[str] | None = None
    model: str | None = None
    max_turns: int | None = None
    enabled_toolsets: frozenset[str] = field(default_factory=frozenset)
    model_router: Any = None
    routing_reason: str = ""
    policy: TurnPolicy = field(default_factory=TurnPolicy)
    # Agent persona sandbox overrides; empty means "inherit the organization setting".
    sandbox_image: str = ""
    cpu_limit: str = ""
    memory_limit: str = ""
    # Knowledge collections the persona may search; empty means every collection the caller can see.
    knowledge_base_ids: tuple[str, ...] = ()
    # Skills the persona may run; empty means every skill the caller can see.
    skill_ids: tuple[str, ...] = ()


@dataclass
class FileDiff:
    path: str
    diff_content: str
    additions: int = 0
    deletions: int = 0
    is_new_file: bool = False


@dataclass
class AgentEvent:
    event_type: str  # "status", "thought", "tool_call", "plan_updated", "approval_required", "interactive_question", "file_diff"
    session_id: str
    payload: dict[str, Any]


class AgentOrchestratorProtocol(ABC):
    """
    Contract for managing conversational turns, sub-agent delegating,
    tool execution loops, and human-in-the-loop pauses.
    """

    @abstractmethod
    def process_user_turn(
        self,
        session_id: str,
        user_message: str,
        model: str | None = None,
        system_prompt: str | None = None,
        system_prompt_parts: SystemPrompt | None = None,
        whitelisted_tools: list[str] | None = None,
        tenant_org_id: str = "default_org",
        tenant_user_id: str = "dev_user",
        max_turns: int | None = None,
        attachments: list[AttachmentRef] | None = None,
        model_router: ModelRouterProtocol | None = None,
        zero_retention: bool = False,
        region: str = "",
        enabled_toolsets: frozenset[str] | None = None,
        callbacks: TurnCallbacks | None = None,
        policy: TurnPolicy | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """
        Executes an agent reasoning & action cycle.
        Streams agent events (status, thoughts, tool calls, diffs).

        ``system_prompt_parts`` (preferred) splits the prompt into a cacheable
        stable prefix and a volatile suffix; ``system_prompt`` remains supported
        for callers with a single opaque prompt. ``attachments`` are the current
        turn's multimodal inputs and are never carried into later turns.
        """
        pass

    @abstractmethod
    async def submit_human_approval(
        self,
        session_id: str,
        action_id: str,
        approved: bool,
    ) -> None:
        """
        Resumes a paused agent execution that was waiting for approval.
        """
        pass

    @abstractmethod
    async def submit_question_answer(
        self,
        session_id: str,
        question_id: str,
        selected_options: list[str],
        custom_text: str | None = None,
    ) -> None:
        """
        Submits the user's answer to an interactive clarification question.
        """
        pass

    @abstractmethod
    async def cancel_turn(self, session_id: str) -> None:
        """Halts the currently running turn immediately."""
        pass
