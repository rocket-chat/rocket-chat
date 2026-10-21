"""
Interface definitions for Agent Workflow & Sub-Agent Orchestration.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from specifications.interfaces.git import SigningKey
from specifications.interfaces.llm import AttachmentRef, ModelRouterProtocol, ToolDefinition
from specifications.interfaces.session import SessionArtifact


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
    # Steps before the delegate must report; 0 lets it work until it has finished.
    max_turns: int = 30
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
    status: str = "completed"  # "completed", "done", "partial", "blocked", "failed", "timeout"
    turns_taken: int = 1
    tool_calls_count: int = 0
    duration_ms: int = 0
    subagent_id: str = ""
    artefacts: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    tokens: int = 0
    cost: float = 0.0
    branch: str | None = None


@dataclass
class SubagentTranscriptRecord:
    """A completed subagent execution transcript and metadata stored durably."""

    id: str
    session_id: str
    org_id: str
    subagent_id: str
    role: str
    task: str
    summary: str
    status: str
    turns_taken: int
    tool_calls_count: int
    duration_ms: int
    tokens: int
    cost_usd: float
    changed_files: list[str]
    artefacts: list[str]
    messages: list[dict[str, Any]]
    created_at: int


class SubagentTranscriptStoreProtocol(Protocol):
    """Storage contract for durable subagent transcripts."""

    async def save_transcript(self, record: SubagentTranscriptRecord) -> None:
        pass

    async def get_transcript(
        self, session_id: str, subagent_id: str, org_id: str
    ) -> SubagentTranscriptRecord | None:
        pass

    async def list_transcripts(
        self, session_id: str, org_id: str
    ) -> list[SubagentTranscriptRecord]:
        pass


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
    # Who acts on GitHub with this credential, for the agent and the audit trail
    # (e.g. "the GitHub App" or "@octocat").
    actor: str = ""


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
    # Credential for the read-only GitHub tools (find repositories, read files, issues, checks).
    # May accept an optional repository slug to resolve repo-specific App installation tokens.
    github_access: Callable[..., Awaitable[GithubAccess]] | None = None
    # Credential for changes on GitHub (comments, reviews, merges, issues) in one repository,
    # resolved on the platform from the organization's policy; never given to a sandbox.
    github_write_access: Callable[[str], Awaitable[GithubAccess]] | None = None
    # Called with (session id, folder) after the agent cloned a repository; returns a note for
    # the agent (for example the outcome of the repository's own setup script) or None.
    repository_cloned: Callable[[str, str], Awaitable[str | None]] | None = None
    # Called with (session id, folder) when the agent makes another repository the active one,
    # so later turns start there too.
    active_repository_chosen: Callable[[str, str], Awaitable[None]] | None = None
    # Called with (session id, tool names) when the agent loads deferred tools, so later turns
    # send them too.
    tools_loaded: Callable[[str, list[str]], Awaitable[None]] | None = None
    # Called with (session id, server -> digest of the guidance the agent has read) when the agent
    # puts a server's usage guidance in effect, so later turns carry it too.
    guidance_read: Callable[[str, dict[str, str]], Awaitable[None]] | None = None
    # Called with (session id, every variable the agent set) after ``env_set``/``env_unset``,
    # so later turns' commands get them too.
    agent_env_changed: Callable[[str, dict[str, str]], Awaitable[None]] | None = None
    # Follow a pull request or branch: (repo, pull request, branch, wake) -> a note for the agent.
    github_watch: Callable[[str, int | None, str | None, bool], Awaitable[str]] | None = None
    # Stop following: (repo, pull request, branch) -> a note for the agent.
    github_unwatch: Callable[[str, int | None, str | None], Awaitable[str]] | None = None
    # Durable wait for external events (CI status, PR review, process exit, time): (target, identifier, timeout_seconds) -> note
    wait_for: Callable[[str, str, int | None], Awaitable[str]] | None = None
    # Called after a background process tool ran so the tracker can mirror the sandbox's
    # processes into the database and announce changes.
    processes_changed: Callable[[str], Awaitable[None]] | None = None
    # How many background processes the session may run at once.
    max_background_processes: int = 5
    # The organization's subagent roles for this turn (None = the built-in defaults).
    subagent_configs: Mapping[str, SubagentConfig] | None = None
    # Scrub secrets/sensitive data from tool execution results before they reach
    # conversation history, events, or the model. Returns (scrubbed_text, count).
    redact_tool_output: Callable[[str], tuple[str, int]] | None = None
    # Persist completed subagent execution transcripts to durable storage
    subagent_transcript_persister: Callable[[SubagentTranscriptRecord], Awaitable[None]] | None = (
        None
    )
    # Durable session artifacts operations
    create_artifact: Callable[[SessionArtifact], Awaitable[SessionArtifact]] | None = None
    update_artifact: Callable[[str, str, str, str], Awaitable[SessionArtifact | None]] | None = None
    get_artifact: Callable[[str, str, str], Awaitable[SessionArtifact | None]] | None = None
    list_artifacts: Callable[[str, str], Awaitable[list[SessionArtifact]]] | None = None


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
    # Tools this caller may never use, whatever the whitelist. Kept apart from it so an
    # unrestricted caller still gets the tools only known once the turn starts (MCP servers).
    denied_tools: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ReasoningPolicy:
    """Reasoning effort or thinking budget configuration for frontier models."""

    effort: str = "medium"  # "low", "medium", "high"
    budget_tokens: int | None = None  # explicit thinking budget tokens (Anthropic / DeepSeek)


@dataclass(frozen=True)
class GenerationParams:
    """Sampling and provider parameters for a turn's model calls (None = provider default)."""

    temperature: float | None = None
    max_tokens: int | None = None
    thinking_budget_tokens: int | None = None
    reasoning_effort: str | None = None
    reasoning: ReasoningPolicy | None = None
    fallback_models: tuple[str, ...] = ()
    # The organization's own context-window sizes, as (model name or fnmatch pattern, tokens),
    # for models the provider library does not know (a BYOK or self-hosted model).
    context_windows: tuple[tuple[str, int], ...] = ()
    # The working size a conversation is kept under (0: the model's whole window). Pruning and
    # compaction thresholds apply to the smaller of the two.
    context_target_tokens: int = 0
    # Provider preferences per model pattern, applied by the gateway (see ``ModelScope``).
    provider_preferences: tuple[tuple[str, dict[str, object]], ...] = ()


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
    # The GPG key (fingerprint) the member's commits are signed with, when they commit as
    # themselves under ``signing_mode == "gpg_key"``.
    signing_key_id: str = ""
    # That key, used on the platform when the branch is published; never given to a sandbox.
    signing_key: SigningKey | None = field(default=None, repr=False)
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
# Reasoning steps a turn may take before the agent must summarise what it has (an agent persona or
# the organization can set another value).
DEFAULT_MAX_TURNS = 100
# A step limit of 0: the turn runs until the model answers, the person cancels it or a cost cap
# is reached. Long coding sessions legitimately take hundreds of steps; a cap there only makes
# the agent wrap up and start over.
NO_STEP_LIMIT = 0


def combine_step_limits(agent_limit: int | None, organization_limit: int) -> int:
    """The step limit of a turn: the agent's own, never beyond the organization's.

    ``NO_STEP_LIMIT`` is the largest value: an agent asking for it under a finite organization
    limit gets that limit, and an unlimited organization lets each agent choose.
    """
    if agent_limit is None:
        return organization_limit
    if organization_limit == NO_STEP_LIMIT:
        return agent_limit
    if agent_limit == NO_STEP_LIMIT:
        return organization_limit
    return min(agent_limit, organization_limit)


TOOL_LOADING_MODES = ("all", "on_demand", "auto")


@dataclass(frozen=True)
class ToolLoading:
    """Which of the agent's tools every request carries, and which it loads when it needs them.

    ``all`` sends every allowed tool. ``on_demand`` sends the platform's own tools (built-in and
    toolset) plus ``active``; every other allowed tool is *deferred*: listed by name, its
    definition sent only once the agent loads it. ``auto`` sends everything until the agent has
    more than ``threshold`` tools, then defers the tools from MCP servers that are not in
    ``active``. ``active`` holds names or shell-style patterns (``mcp__signoz*__*search*``).
    """

    mode: str = "all"
    active: tuple[str, ...] = ()
    threshold: int = 60
    # Deferred tools the session already loaded; they stay loaded for later turns.
    loaded: tuple[str, ...] = ()


@dataclass(frozen=True)
class ServerGuidance:
    """What the organization tells the agent about one MCP server beyond its tools.

    Only servers with a ``text`` appear: a server without guidance costs the agent nothing.
    """

    server: str  # the <server> of ``mcp__<server>__<tool>``
    trigger: str  # one line: when to use the server (the first line of ``text``)
    text: str  # how to use it, in markdown; may be long
    digest: str  # of ``text``: an edited guidance has to be read again


@dataclass(frozen=True)
class McpGuidance:
    """The usage guidance of the turn's MCP servers, and which the session has already read."""

    servers: tuple[ServerGuidance, ...] = ()
    # server -> digest of the guidance the agent put in effect earlier in the session.
    read: Mapping[str, str] = field(default_factory=dict)


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
    # The session's active repository folder (``/workspace/<name>``): where git, test and build
    # tools act when the agent names no folder. Empty until the session clones one.
    active_repository: str = ""
    # Names of the variables the sandbox type sets (values stay on the platform), and the
    # variables the agent set with ``env_set`` in earlier turns of the session.
    sandbox_env_names: tuple[str, ...] = ()
    agent_env: Mapping[str, str] = field(default_factory=dict)
    tool_loading: ToolLoading = field(default_factory=ToolLoading)
    mcp_guidance: McpGuidance = field(default_factory=McpGuidance)
    output_offload_threshold: int = 6_000
    max_turn_cost_usd: float | None = None
    sandbox_profile: str = ""


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
    # The sandbox type (catalogue entry) the agent's sessions start in when the session's
    # creator chose none; empty means the organization's default.
    sandbox_id: str = ""
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
    event_type: str  # "status", "thought", "tool_call", "plan_updated", "approval_required", "interactive_question", "file_diff", "command_output"
    session_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class SteeringMessage:
    """A message the person sent while a turn of the session was running.

    The running turn takes it at its next step, as a message of the conversation, instead of
    the person waiting for the end of a turn that may run for an hour.
    """

    content: str
    message_id: str | None = None
    author_user_id: str | None = None


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
        denied_tools: frozenset[str] | None = None,
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
        synthetic_prompt: bool = False,
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

    def steer(self, session_id: str, message: SteeringMessage) -> bool:
        """Hand ``message`` to the session's running turn, if this process runs one.

        ``False`` means no turn here can take it: the caller starts a turn instead, which waits
        for any turn still running elsewhere.
        """
        return False
