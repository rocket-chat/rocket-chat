"""
Interface definitions for Agent Workflow & Sub-Agent Orchestration.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, TypedDict

from specifications.interfaces.git import SigningKey
from specifications.interfaces.llm import (
    AttachmentRef,
    CredentialBinding,
    ModelRouterProtocol,
    ToolDefinition,
)
from specifications.interfaces.sandbox import WorkspaceSpec
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


# Which model a role runs on when it names none: ``fast`` is the organization's fast model (cheap
# searching and reading), ``default`` what the routing mode gives subagents, ``frontier`` the
# session's own model.
SUBAGENT_TIERS: tuple[str, ...] = ("fast", "default", "frontier")
# Where a role's changes land: the shared tree (``none``), a git worktree of its own on a
# ``subagent/<id>`` branch, or a sandbox of its own seeded from the session's workspace.
SUBAGENT_ISOLATIONS: tuple[str, ...] = ("none", "worktree", "sandbox")
# What a child starts with besides its role prompt and the task.
SUBAGENT_INHERITABLE: tuple[str, ...] = ("agents_md", "memory", "checklist", "skills")
DEFAULT_SUBAGENT_INHERIT: tuple[str, ...] = ("agents_md", "memory", "checklist")
# A child's own budget: steps before it must report, and dollars (0 for either: no own limit;
# the turn's limits still apply).
DEFAULT_SUBAGENT_STEPS = 40
DEFAULT_SUBAGENT_COST_USD = 3.0
SUBAGENT_STATUSES: tuple[str, ...] = ("completed", "partial", "blocked", "failed", "cancelled")
FINDING_SEVERITIES: tuple[str, ...] = ("critical", "high", "medium", "low", "info")
# Where a role definition comes from: shipped, the organization's, or a repository's
# ``.rocket/agents/*.md``.
SUBAGENT_SOURCES: tuple[str, ...] = ("builtin", "org", "repository")


@dataclass
class SubagentConfig:
    """Configurable behavioral definition for a specialized subagent."""

    id: str
    name: str
    role_title: str
    # When to use the role, and when not to: the parent reads it in the roles index.
    description: str
    system_prompt: str
    # A named model wins over ``tier``.
    model: str | None = None
    tier: str = "default"
    # Steps before the delegate must report; 0 lets it work until it has finished.
    max_turns: int = DEFAULT_SUBAGENT_STEPS
    whitelisted_tools: list[str] = field(default_factory=lambda: ["*"])
    disallowed_tools: list[str] = field(default_factory=list)
    temperature: float = 0.2
    reasoning_effort: str | None = None
    max_cost_usd: float = DEFAULT_SUBAGENT_COST_USD
    max_wall_seconds: int = 0
    isolation: str = "none"
    # Enforced: a child that may not ask has its approvals and questions refused.
    can_ask_user: bool = False
    can_delegate: bool = False
    inherit: list[str] = field(default_factory=lambda: list(DEFAULT_SUBAGENT_INHERIT))
    # JSON Schema the ``data`` of the child's report must follow (None: no structured data).
    output_schema: dict[str, Any] | None = None
    enabled: bool = True
    source: str = "builtin"


@dataclass
class SubagentTaskRequest:
    """Request payload to delegate a task to a subagent."""

    role: str
    task: str
    context: str = ""
    subagent_id: str | None = None
    # Returns a handle at once; the result is read with ``subagent_status`` or ``wait_for``.
    background: bool = False
    # The parent's ``delegate_subagent`` call, so a client nests the child's steps under it.
    parent_tool_call_id: str | None = None
    # Skills put in effect for the child (they are inherited only when named).
    skills: list[str] = field(default_factory=list)


@dataclass
class SubagentFinding:
    """One thing a child found: where, how serious, and what shows it."""

    file: str = ""
    line: int | None = None
    severity: str = "info"
    evidence: str = ""


@dataclass
class SubagentChanges:
    """What a child changed, computed from ``git diff`` in its tree (not from its tool calls)."""

    branch: str | None = None
    diff_stat: str = ""
    files: list[str] = field(default_factory=list)


@dataclass
class SubagentReport:
    """The structured result a child ends with (its ``report`` call)."""

    status: str
    summary: str
    findings: list[SubagentFinding] = field(default_factory=list)
    changes: SubagentChanges | None = None
    open_questions: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    # Follows the role's ``output_schema`` when it has one.
    data: dict[str, Any] | None = None


@dataclass
class SubagentTaskResult:
    """Structured response returned by a subagent upon completing its delegated mission."""

    role: str
    task: str
    summary: str
    status: str = "completed"  # one of SUBAGENT_STATUSES
    turns_taken: int = 1
    tool_calls_count: int = 0
    duration_ms: int = 0
    subagent_id: str = ""
    artefacts: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    tokens: int = 0
    cost: float = 0.0
    branch: str | None = None
    report: SubagentReport | None = None
    model: str = ""
    isolation: str = "none"
    background: bool = False
    parent_tool_call_id: str | None = None


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
    # The child's whole structured report (the parent sees a clamped rendering of it).
    report: dict[str, Any] | None = None
    model: str = ""


# A background child's run: queued until its task starts, running while it works (its lease
# renewed), then one of SUBAGENT_STATUSES.
SUBAGENT_RUN_ACTIVE: tuple[str, ...] = ("queued", "running")
# How long a running child's lease lasts without renewal before another replica may recover it.
SUBAGENT_LEASE_SECONDS = 90


@dataclass
class SubagentRunRecord:
    """A delegated child as a supervised task (``subagent_runs``), so it outlives its turn."""

    id: str
    org_id: str
    session_id: str
    subagent_id: str
    role: str
    task: str
    context: str = ""
    status: str = "queued"
    background: bool = False
    parent_tool_call_id: str | None = None
    lease_owner: str = ""
    lease_expires_at: int = 0
    attempts: int = 0
    # The ``SubagentTaskResult`` once the child has finished, as plain data.
    result: dict[str, Any] | None = None
    cost_usd: float = 0.0
    created_at: int = 0
    updated_at: int = 0


class SubagentRunStoreProtocol(Protocol):
    """Storage contract for supervised subagent runs (forced row-level security on ``org_id``)."""

    async def save_run(self, record: SubagentRunRecord) -> None:
        """Insert or replace the run ``record.id``."""
        ...

    async def get_run(
        self, org_id: str, session_id: str, subagent_id: str
    ) -> SubagentRunRecord | None: ...

    async def list_runs(self, org_id: str, session_id: str) -> list[SubagentRunRecord]: ...

    async def renew_leases(self, owner: str, until: int) -> int:
        """Extend the lease of every active run ``owner`` holds; how many were renewed."""
        ...

    async def claim_expired(self, owner: str, now: int, until: int) -> list[SubagentRunRecord]:
        """Atomically take the active runs whose lease ran out (administrative connection)."""
        ...


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
    # What the person is asked to allow, as the client shows it (see ``ApprovalDetails``).
    tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    diff: str = ""
    repository: str = ""


# Where a remembered approval applies: every later call of the tool in the session, or the
# same command in the same repository in every session of the member who saved it.
APPROVAL_RULE_SCOPES: tuple[str, ...] = ("session", "repository")
# Bounds on what an approval request shows (event payloads, never the model's context).
MAX_APPROVAL_ARGUMENT_CHARS = 2_000
MAX_APPROVAL_ARGUMENTS_CHARS = 8_000
MAX_APPROVAL_DIFF_CHARS = 20_000


@dataclass(frozen=True)
class ApprovalDetails:
    """The structured call behind a gate: shown to the person, and what a rule is saved from.

    ``arguments`` and ``diff`` are already bounded by the caller (``MAX_APPROVAL_*``);
    ``repository`` is the active repository's ``owner/name`` (empty when unknown).
    """

    tool: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    diff: str = ""
    repository: str = ""


@dataclass(frozen=True)
class ApprovalRule:
    """A remembered approval: the gate it answers is not raised again.

    ``command`` empty matches every call of ``tool``; otherwise the call's ``command`` must be
    the same text (whitespace-normalised). ``repository`` is set for the ``repository`` scope.
    """

    id: str
    scope: str
    tool: str
    command: str = ""
    repository: str = ""


@dataclass(frozen=True)
class RememberedApprovals:
    """The rules a turn may apply, and the risk levels they may answer.

    The platform leaves ``risks`` empty when the organization switched remembered approvals off
    or locked the permission mode, and never includes ``critical``: an irreversible call always
    waits for a person.
    """

    rules: tuple[ApprovalRule, ...] = ()
    risks: frozenset[str] = frozenset()


class ApprovalCallback(Protocol):
    """Raises a human gate and waits for the answer (``APPROVAL_GRANTED_PREFIX`` when allowed)."""

    def __call__(
        self,
        action_type: str,
        description: str,
        risk_level: str,
        details: ApprovalDetails | None = None,
    ) -> Awaitable[str]: ...


@dataclass
class InteractiveQuestion:
    question_id: str
    question_text: str
    options: list[str]
    is_multi_select: bool = False
    default_recommended_option: str | None = None


# The ``action_type`` of the gate ``exit_plan_mode`` raises: the cockpit shows it as a plan card.
PLAN_APPROVAL_ACTION = "execute_plan"
# Bounds on what a plan decision carries back to the agent (it reaches the model's context).
MAX_PLAN_TASKS = 30
MAX_PLAN_TASK_CHARS = 300
MAX_PLAN_FEEDBACK_CHARS = 4_000


@dataclass(frozen=True)
class PlanTaskEdit:
    """One task of a plan as the person left it on the plan card."""

    id: str
    title: str
    description: str = ""


@dataclass(frozen=True)
class PlanDecision:
    """What the person decided on a proposed plan.

    ``permission_mode`` is the mode the rest of the work runs in, already checked against the
    organization's lock and the member's ``use_bypass_mode`` capability (empty when rejected).
    ``checklist`` is set only when the person edited the plan; ``feedback`` is free text.
    Every field is bounded by the ``MAX_PLAN_*`` limits before it is stored.
    """

    approved: bool
    permission_mode: str = ""
    checklist: tuple[PlanTaskEdit, ...] = ()
    feedback: str = ""
    decided_at: float = 0.0


# Rendered between the cacheable prefix and the volatile suffix of a system prompt.
DYNAMIC_SECTION_HEADER = "\n\n# --- Session Context (volatile; not cacheable) ---\n"


class RunningTurnLive(TypedDict):
    """What a running turn has streamed since its last recorded step.

    ``seq`` is the session's event sequence the snapshot covers through: every streamed token
    up to it is in ``current_reasoning`` / ``current_text``, and none after it is.
    """

    current_reasoning: str
    current_text: str
    seq: int
    step_ids: list[str]


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

    async def fetch_running_turn(self, session_id: str) -> RunningTurnLive | None:
        """The live part of a turn another replica runs; ``None`` if none answers in time."""
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


@dataclass(frozen=True)
class FolderSetupOutcome:
    """What preparing a folder before a command did: a note for the agent, and whether the
    command may run (a failed setup stops it, so the agent fixes the setup first)."""

    note: str
    ok: bool


@dataclass(frozen=True)
class WaitOutcome:
    """What ``wait_for`` did: the note for the agent, and whether something will wake the session.

    ``registered`` is false when the condition had already settled (CI finished on the commit,
    no check will ever run): the turn goes on, and the agent acts on ``message`` at once.
    """

    message: str
    registered: bool


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
    # Runs a code skill (its output) or loads an instruction skill, whose instructions come back
    # as ``SkillInstructions`` so the engine keeps them in effect instead of in a tool result.
    execute_skill: Callable[[str, dict[str, Any]], Awaitable[str | SkillInstructions]] | None = None
    # Called with (session id, skill -> digest of the instructions in effect) when the agent puts
    # a skill in effect, so later turns carry it too.
    skills_read: Callable[[str, dict[str, str]], Awaitable[None]] | None = None
    question: Callable[[str, list[str], bool, str | None], Awaitable[str]] | None = None
    approval: ApprovalCallback | None = None
    # The approvals the person remembered that apply to this session (read before every gated
    # call, so a rule saved or revoked during the turn applies at once).
    approval_rules: Callable[[str], Awaitable[RememberedApprovals]] | None = None
    # Called with (rule, tool name, risk level) when a rule answered a gate, for the audit.
    approval_rule_used: Callable[[ApprovalRule, str, str], Awaitable[None]] | None = None
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
    # Durable wait for external events (CI status, PR review, process exit, time):
    # (target, identifier, timeout_seconds, commit sha or None) -> what happened. Only a wait
    # that was registered ends the turn; one that settled at once is the agent's to act on.
    wait_for: Callable[[str, str, int | None, str | None], Awaitable[WaitOutcome]] | None = None
    # Called with (session id, "owner/repo", branch, head sha) after a branch was published, so
    # a CI wait reads the commit just pushed, not the pull request's previous head.
    branch_published: Callable[[str, str, str, str], Awaitable[None]] | None = None
    # Called with (session id, absolute folder) before a command runs there: runs the
    # organization's repository setups that apply and have not run, or returns None.
    prepare_folder: Callable[[str, str], Awaitable[FolderSetupOutcome | None]] | None = None
    # Called after a background process tool ran so the tracker can mirror the sandbox's
    # processes into the database and announce changes.
    processes_changed: Callable[[str], Awaitable[None]] | None = None
    # How many background processes the session may run at once.
    max_background_processes: int = 5
    # The organization's subagent roles for this turn (None = the built-in defaults).
    subagent_configs: Mapping[str, SubagentConfig] | None = None
    # Resolves a role's model: its own, else its tier through the organization's routing.
    subagent_model: Callable[[SubagentConfig], str] | None = None
    # Supervised runs of children (None: background delegation is unavailable).
    subagent_runs: SubagentRunStoreProtocol | None = None
    # Memories relevant to a child's task, already bounded (None: no memory inheritance).
    subagent_memories: Callable[[str], Awaitable[str]] | None = None
    # The workspace a sandbox-isolated child's own sandbox is created from, by sandbox id.
    subagent_sandbox_spec: Callable[[str], Awaitable[WorkspaceSpec]] | None = None
    # How many children with edit or command tools may run at once, from the session sandbox's
    # CPU and memory (read-only children always run in parallel).
    max_parallel_writers: int = 1
    # Where a background child's events go once the turn that started it has ended.
    subagent_event_sink: Callable[[AgentEvent], Awaitable[None]] | None = None
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
    # Called with (session id, epoch seconds the plan was proposed) once its approval gate is
    # answered: what the person decided on the plan card (mode, edited checklist, feedback), or
    # None when the gate was answered without one (a plain approval, Slack).
    plan_decision: Callable[[str, float], Awaitable[PlanDecision | None]] | None = None


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
    # Estimated reasoning tokens a step may spend before it acts; past it the step is asked
    # again with reasoning off (0: no limit).
    reasoning_step_limit_tokens: int = 0
    fallback_models: tuple[str, ...] = ()
    # The organization's own context-window sizes, as (model name or fnmatch pattern, tokens),
    # for models the provider library does not know (a BYOK or self-hosted model).
    context_windows: tuple[tuple[str, int], ...] = ()
    # The working size a conversation is kept under (0: the model's whole window). Pruning and
    # compaction thresholds apply to the smaller of the two.
    context_target_tokens: int = 0
    # Provider preferences per model pattern, applied by the gateway (see ``ModelScope``).
    provider_preferences: tuple[tuple[str, dict[str, object]], ...] = ()
    # Shares of the working context at which old tool results are pruned and the conversation
    # compacted (``models.context_prune_percent`` / ``context_compact_percent``); 0 keeps the
    # context manager's own (70% / 85%).
    context_prune_ratio: float = 0.0
    context_compact_ratio: float = 0.0


GIT_PUBLISH_AS: tuple[str, ...] = ("member", "bot")
GIT_SIGNING: tuple[str, ...] = ("github", "member_gpg", "none")


@dataclass(frozen=True)
class GitPolicy:
    """Organization git policy plus the member's identity, resolved once per turn.

    ``publish_as`` (``github.publish_as``): ``member`` opens pull requests and acts on GitHub
    with the member's linked token when there is one, ``bot`` always through the GitHub App.
    Commits the bot makes credit the member with a ``Co-authored-by`` trailer either way.

    ``signing`` (``github.signing``): ``github`` creates the commits through GitHub's API so
    GitHub signs them; ``member_gpg`` makes the member commit as themselves, signed with their
    imported key (a member without one gets ``github``); ``none`` publishes them unsigned.
    """

    publish_as: str = "member"
    signing: str = "github"
    default_branch: str = "main"
    bot_name: str = "RocketChat Bot"
    bot_email: str = "bot@rocketchat.internal"
    user_name: str = ""
    user_email: str = ""
    user_token: str | None = field(default=None, repr=False)
    # The GPG key (fingerprint) the member's commits are signed with, when they commit as
    # themselves under ``signing == "member_gpg"``.
    signing_key_id: str = ""
    # That key, used on the platform when the branch is published; never given to a sandbox.
    signing_key: SigningKey | None = field(default=None, repr=False)
    # (installation id, account login) pairs the organization mapped; the only GitHub App
    # installations a bot-authored push or pull request may use.
    app_installations: tuple[tuple[int, str], ...] = ()

    @property
    def commits_as_member(self) -> bool:
        """The member's own name is on the commits (``member_gpg``, once their identity is known)."""
        return self.signing == "member_gpg" and bool(self.user_name)

    @property
    def publishes_as_member(self) -> bool:
        return self.publish_as == "member"


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


# The tools every request of every agent carries (when the agent may use them). Everything else
# an agent may use is deferred: listed by name in the cached prompt and loaded on demand. Long
# turns re-send every definition at every step, and a model choosing among eighty tools chooses
# worse than among twenty (opencode sends fifteen, Codex about ten).
CORE_TOOLS: tuple[str, ...] = (
    "bash_exec",
    "file_read",
    "file_edit",
    "file_write",
    "apply_patch",
    "grep_search",
    "glob_files",
    "update_task_checklist",
    "web_fetch",
    "web_search",
    "execute_skill",
    "ask_question",
    "clone_repository",
    "git_commit",
    "git_push",
    "git_open_pr",
    "github_pull_request",
    "github_actions",
    "wait_for",
)
# Past this many tools sent with every step (core, ticked and plumbing), the agent settings warn.
ACTIVE_TOOLS_WARNING = 30


@dataclass(frozen=True)
class ToolLoading:
    """Which of the agent's tools every request carries, and which it loads when it needs them.

    Every request carries ``CORE_TOOLS``, the loading tools, and what the agent keeps ``active``
    (names or shell-style patterns such as ``mcp__signoz*__*search*``), each only when the agent
    may use it. Every other allowed tool is deferred: *listed by name*, its definition sent once
    the agent loads it. ``send_all`` sends every allowed tool: the default for an orchestrator
    driven without the platform's policy, and for a turn with no agent (a model with every tool);
    the platform builds every agent's policy with it off (``tool_loading_for``).
    """

    active: tuple[str, ...] = ()
    # Deferred tools the session already loaded; they stay loaded for later turns.
    loaded: tuple[str, ...] = ()
    send_all: bool = True


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
class SkillInstructions:
    """The instructions of an instruction skill: kept in effect for the session, not in a result."""

    name: str
    body: str


@dataclass(frozen=True)
class SkillsInEffect:
    """The skills whose instructions the session put in effect, as of the start of a turn."""

    # skill -> digest of the instructions the agent read, in the order it read them.
    read: Mapping[str, str] = field(default_factory=dict)
    # skill -> its instructions as they are now, for the skills in ``read`` the member can still use.
    texts: Mapping[str, str] = field(default_factory=dict)


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
    skills: SkillsInEffect = field(default_factory=SkillsInEffect)
    output_offload_threshold: int = 6_000
    max_turn_cost_usd: float | None = None
    sandbox_profile: str = ""
    attribution: ModelAttribution = field(default_factory=lambda: ModelAttribution())
    # ``general.plan_critic``: a ``reviewer`` subagent reads a plan before the person decides.
    plan_critic: bool = False


@dataclass(frozen=True)
class ModelAttribution:
    """Who and what a turn's model calls are for, beyond the session (``ModelScope``)."""

    # The person whose message started the turn (the session's owner when it is them).
    requester_user_id: str | None = None
    agent_id: str | None = None
    credential_bindings: tuple[CredentialBinding, ...] = ()
    strict_binding: bool = False
    turn_id: str | None = None


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
    # The sandbox type (catalogue entry) the agent's sessions start in when the session's
    # creator chose none; empty means the organization's default. The type holds the image,
    # CPU and memory: an agent sets no resources of its own.
    sandbox_id: str = ""
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
    # "status", "thought", "tool_call", "plan_updated", "approval_required",
    # "interactive_question", "file_diff", "command_output", and "answer": the text the turn
    # answers with (payload ``content``), sent just before its ``completed`` status. It can
    # differ from the text streamed in the last step (a draft kept across a verification step).
    event_type: str
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

    def is_running(self, session_id: str) -> bool:
        """Whether a turn of ``session_id`` runs in this process."""
        return False

    def withdraw_steering(self, session_id: str, message_id: str) -> None:
        """Drop a steering message the running turn has not taken yet."""
        return None

    def steer(self, session_id: str, message: SteeringMessage) -> bool:
        """Hand ``message`` to the session's running turn, if this process runs one.

        ``False`` means no turn here can take it: the caller starts a turn instead, which waits
        for any turn still running elsewhere.
        """
        return False
