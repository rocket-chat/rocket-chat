"""The payload of every session event, as a typed contract.

``SessionEvent.payload`` is a plain dict on the wire, which is easy to drift: a publisher adds a
key, a client reads another. These ``TypedDict`` definitions are the reviewed shape of each
event type. The in-process event bus validates every published event against them when
``ROCKET_VALIDATE_EVENTS=1`` (the test suite sets it), so a payload change fails the tests
that exercise it until the contract and the WebSocket protocol documentation are updated.

Keys marked ``NotRequired`` are optional on the wire; unknown keys are rejected.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from specifications.interfaces.events import EventType


class TokenPayload(TypedDict):
    delta: str


class RunningTurnPayload(TypedDict, total=False):
    """Metadata and completed steps of a turn actively in flight."""

    turn_index: int
    started_at: int
    model: NotRequired[str]
    status: NotRequired[str]
    steps: list[dict[str, Any]]
    current_reasoning: NotRequired[str]


class StatusPayload(TypedDict):
    """Progress of a turn. ``final`` marks the runner's closing event (answer stored)."""

    status: NotRequired[str]
    state: NotRequired[str]
    # While a step runs tools, "running" carries a short action derived from the tool names
    # (``agent_core.step_status``); the model's own text travels as ``token`` events.
    message: NotRequired[str]
    error: NotRequired[str]
    final: NotRequired[bool]
    action_id: NotRequired[str]
    question_id: NotRequired[str]
    sandbox_status: NotRequired[str]
    turns_taken: NotRequired[int]
    max_turns: NotRequired[int]
    # The model call a running turn is on, counted from 1.
    step: NotRequired[int]
    # On a failed turn's closing status: what failed (see ``TurnErrorKind``).
    error_kind: NotRequired[str]
    # On the runner's first "running" status of a turn, with the id of the message that
    # started it when the sender gave one.
    turn_start: NotRequired[bool]
    message_id: NotRequired[str]
    reason: NotRequired[str]
    # On the turn's "running" status: the agent (empty for a raw turn) and model it runs with.
    agent_id: NotRequired[str]
    agent_name: NotRequired[str]
    model: NotRequired[str]
    discard_partial: NotRequired[bool]
    context_tokens: NotRequired[int]
    context_limit: NotRequired[int]
    context_model_limit: NotRequired[int]
    context_target_tokens: NotRequired[int]
    context_source: NotRequired[str]
    context_tier2_tokens: NotRequired[int]
    context_tier3_tokens: NotRequired[int]


class ToolStartedPayload(TypedDict):
    id: NotRequired[str | None]
    name: str
    arguments: dict[str, Any]
    created_at: NotRequired[int]
    preface: NotRequired[str]
    # A subagent's own step: the child, and the parent's ``delegate_subagent`` call it runs
    # under (absent for the agent's own steps).
    subagent_id: NotRequired[str]
    parent_tool_call_id: NotRequired[str | None]


class ToolCompletedPayload(TypedDict):
    id: NotRequired[str | None]
    name: str
    arguments: dict[str, Any]
    result: Any
    status: str
    duration_ms: NotRequired[int | float]
    redacted: NotRequired[int]
    created_at: NotRequired[int]
    subagent_id: NotRequired[str]
    parent_tool_call_id: NotRequired[str | None]


class FileDiffPayload(TypedDict):
    path: str
    diff_content: str
    additions: NotRequired[int]
    deletions: NotRequired[int]
    is_new_file: NotRequired[bool]


class DecisionRequiredPayload(TypedDict):
    question_id: str
    question_text: str
    options: NotRequired[list[str]]
    is_multi_select: NotRequired[bool]
    default_recommended_option: NotRequired[str | None]
    # A question a subagent asked: which child, and its role.
    subagent_id: NotRequired[str]
    subagent_role: NotRequired[str]


class DecisionSubmittedPayload(TypedDict):
    question_id: str
    selected_options: NotRequired[list[str]]
    custom_text: NotRequired[str | None]


class ApprovalRequiredPayload(TypedDict):
    action_id: str
    action_type: str
    command_or_path: str
    risk_level: str
    policy_decision: NotRequired[str]
    metadata: NotRequired[dict[str, Any]]
    # The structured call (bounded), a diff for an edit, and the repository it acts in.
    tool: NotRequired[str]
    arguments: NotRequired[dict[str, Any]]
    diff: NotRequired[str]
    repository: NotRequired[str]
    # How the person may remember a "yes": "session", "repository" (empty when none applies).
    remember_options: NotRequired[list[str]]
    # An approval a subagent needs: which child, and its role.
    subagent_id: NotRequired[str]
    subagent_role: NotRequired[str]


class ApprovalRulesChangedPayload(TypedDict):
    # "added" or "revoked"; clients read the session's rules again.
    change: str
    rule_id: str
    scope: str


class LogChunkPayload(TypedDict):
    text: str
    # The tool call the text belongs to; absent for output not tied to a call.
    tool_call_id: NotRequired[str]


class SessionUpdatedPayload(TypedDict):
    title: NotRequired[str]
    session_id: NotRequired[str]
    is_shared: NotRequired[bool]
    collaborators: NotRequired[list[str]]
    is_pinned: NotRequired[bool]
    is_archived: NotRequired[bool]
    is_processing: NotRequired[bool]
    # "approval" or "question" while a turn waits on a person, "" once it does not.
    awaiting_input: NotRequired[str]
    # How the latest turn ended: completed, waiting or error.
    last_turn_status: NotRequired[str]


class UserMessagePayload(TypedDict):
    message_id: str
    role: str
    content: str
    author_user_id: str | None


class MessagePendingPayload(TypedDict):
    message_id: str
    # "steer": taken by the running turn at its next step; "queue": run as the next turn.
    delivery: str
    content: str
    author_user_id: str | None


class MessageCancelledPayload(TypedDict):
    message_id: str


class PlanUpdatedPayload(TypedDict):
    tasks: list[dict[str, Any]]
    session_id: NotRequired[str]


class FollowupSuggestionPayload(TypedDict):
    suggestion: str


class SubagentStartedPayload(TypedDict):
    subagent_id: str
    role: str
    name: NotRequired[str]
    task: str
    # The parent's ``delegate_subagent`` call the child's steps nest under.
    parent_tool_call_id: NotRequired[str | None]
    model: NotRequired[str]
    # "none", "worktree" or "sandbox".
    isolation: NotRequired[str]
    background: NotRequired[bool]


class SubagentProgressPayload(TypedDict):
    """The child's streamed text, merged (at most one event per 250 ms or 200 characters)."""

    subagent_id: str
    delta: NotRequired[str]
    is_reasoning: NotRequired[bool]
    tool: NotRequired[str]
    status: NotRequired[str]
    message: NotRequired[str]


class SubagentCompletedPayload(TypedDict):
    subagent_id: str
    role: str
    task: str
    summary: str
    duration_ms: NotRequired[int]
    turns_taken: NotRequired[int]
    tool_calls_count: NotRequired[int]
    # One of ``SUBAGENT_STATUSES``: completed, partial, blocked, failed, cancelled.
    status: NotRequired[str]
    tokens: NotRequired[int]
    cost: NotRequired[float]
    changed_files: NotRequired[list[str]]
    artefacts: NotRequired[list[str]]
    parent_tool_call_id: NotRequired[str | None]
    model: NotRequired[str]
    # The worktree or sandbox branch holding the child's changes, to merge or reject.
    branch: NotRequired[str | None]
    # The whole structured report (``SubagentReport`` as plain data).
    report: NotRequired[dict[str, Any] | None]


class PostFailureSynthesisPayload(TypedDict):
    reason: str
    error: NotRequired[str]
    max_turns: NotRequired[int]
    turns_taken: NotRequired[int]


# What ended a turn, for the client's way out: retry, another model, the settings to fix, or
# (``access``) another agent.
TurnErrorKind = Literal["provider", "budget", "sandbox", "model", "access", "internal"]


class SpendLimitPayload(TypedDict):
    """A spend limit as the member sees it (``budget_warning``)."""

    limit_id: str
    # ``org``, ``member``, ``agent``, ``agent_member`` or ``slack_workspace``.
    subject: str
    subject_id: str
    # ``day``, ``week``, ``month``, ``rolling_24h``, ``rolling_7d``, ``session`` or ``turn``.
    period: str
    mode: str
    label: str
    spent: float
    amount: float
    # Epoch seconds when room frees up; ``None`` for a session or turn limit.
    resets_at: int | None


class BudgetBlockedPayload(TypedDict):
    """A spend limit refused the turn's next model call; nothing was bought."""

    limit_id: str
    subject: str
    period: str
    spent: float
    amount: float
    resets_at: int | None
    message: str


class AgentAccessDeniedPayload(TypedDict):
    """The person who sent the message may not use the session's agent (revoked or never
    granted); the turn did not start, and the client asks them to pick another agent."""

    agent_id: str
    agent_name: str
    message: str


class PlanDecidedPayload(TypedDict):
    """A person answered a plan card: the gate ``action_id`` is released."""

    action_id: str
    approved: bool
    # The mode the rest of the work runs in; empty when the plan was rejected.
    permission_mode: str
    # Whether the person edited the checklist before approving.
    edited: bool
    decided_by: str


class GitPublishedPayload(TypedDict):
    """A person committed, pushed or opened a pull request from the cockpit's Git tab."""

    # ``commit``, ``push`` or ``pull_request``.
    action: str
    repository: str
    # What the publishing path answered, for the conversation (bounded).
    summary: str
    url: NotRequired[str]
    decided_by: str


class ErrorPayload(TypedDict):
    error: str
    # One of ``TurnErrorKind``.
    error_kind: NotRequired[str]


class ProcessPayload(TypedDict):
    process_id: str
    name: str
    command: str
    state: str
    port: int | None
    exit_code: int | None
    started_at: int
    ended_at: int | None


class GithubEventPayload(TypedDict):
    kind: str
    repo: str
    pr_number: int | None
    summary: str
    outcome: str
    url: str
    actor: str
    at: int
    woke: bool


class SandboxStatePayload(TypedDict, total=False):
    # "failed" means the sandbox could not start; ``message`` says why (e.g. the image cannot
    # be pulled). The turn is not over: the agent receives the same error as a tool result.
    state: str
    status: str
    message: str


class ContextCompactedPayload(TypedDict):
    tokens_before: int
    tokens_after: int
    compacted_messages_count: int
    summary: NotRequired[str | None]
    # "model", "model_retry" or "digest" (rebuilt from the tool history), see session_compactions.
    summary_status: NotRequired[str]


class GoalUpdatedPayload(TypedDict):
    goal_id: str
    description: str
    status: str
    consumed_tokens: int
    consumed_cost_usd: float
    continuation_count: int
    max_continuations: int
    stop_condition: NotRequired[str | None]


class ContinuationScheduledPayload(TypedDict):
    continuation_index: int
    max_continuations: int
    reason: str
    goal_id: NotRequired[str | None]


class WaitStartedPayload(TypedDict):
    wait_id: str
    wait_type: str
    target: str
    timeout_seconds: NotRequired[int | None]
    reason: NotRequired[str | None]
    # ci_status: the commit whose checks are awaited (the head the session last pushed).
    sha: NotRequired[str]


class WaitCompletedPayload(TypedDict):
    wait_id: str
    wait_type: str
    target: str
    status: str
    summary: NotRequired[str | None]
    payload: NotRequired[dict[str, Any]]


class ArtifactCreatedPayload(TypedDict):
    id: str
    title: str
    filename: str
    artifact_type: str
    version: int
    content: NotRequired[str | None]
    metadata: NotRequired[dict[str, Any]]
    created_at: int


class ArtifactUpdatedPayload(TypedDict):
    id: str
    title: str
    filename: str
    artifact_type: str
    version: int
    content: NotRequired[str | None]
    updated_at: int


PAYLOAD_TYPES: dict[EventType, Any] = {
    EventType.TOKEN: TokenPayload,
    EventType.REASONING_TOKEN: TokenPayload,
    EventType.STATUS_CHANGED: StatusPayload,
    EventType.TOOL_STARTED: ToolStartedPayload,
    EventType.TOOL_COMPLETED: ToolCompletedPayload,
    EventType.FILE_DIFF: FileDiffPayload,
    EventType.DECISION_REQUIRED: DecisionRequiredPayload,
    EventType.DECISION_SUBMITTED: DecisionSubmittedPayload,
    EventType.APPROVAL_REQUIRED: ApprovalRequiredPayload,
    EventType.APPROVAL_RULES_CHANGED: ApprovalRulesChangedPayload,
    EventType.LOG_CHUNK: LogChunkPayload,
    EventType.SANDBOX_STATE: SandboxStatePayload,
    EventType.SESSION_UPDATED: SessionUpdatedPayload,
    EventType.USER_MESSAGE: UserMessagePayload,
    EventType.MESSAGE_PENDING: MessagePendingPayload,
    EventType.MESSAGE_CANCELLED: MessageCancelledPayload,
    EventType.PLAN_UPDATED: PlanUpdatedPayload,
    EventType.FOLLOWUP_SUGGESTION: FollowupSuggestionPayload,
    EventType.SUBAGENT_STARTED: SubagentStartedPayload,
    EventType.SUBAGENT_PROGRESS: SubagentProgressPayload,
    EventType.SUBAGENT_COMPLETED: SubagentCompletedPayload,
    EventType.POST_FAILURE_SYNTHESIS: PostFailureSynthesisPayload,
    EventType.PROCESS_STARTED: ProcessPayload,
    EventType.PROCESS_EXITED: ProcessPayload,
    EventType.GITHUB_EVENT: GithubEventPayload,
    EventType.CONTEXT_COMPACTED: ContextCompactedPayload,
    EventType.GOAL_UPDATED: GoalUpdatedPayload,
    EventType.CONTINUATION_SCHEDULED: ContinuationScheduledPayload,
    EventType.WAIT_STARTED: WaitStartedPayload,
    EventType.WAIT_COMPLETED: WaitCompletedPayload,
    EventType.ARTIFACT_CREATED: ArtifactCreatedPayload,
    EventType.ARTIFACT_UPDATED: ArtifactUpdatedPayload,
    EventType.BUDGET_WARNING: SpendLimitPayload,
    EventType.BUDGET_BLOCKED: BudgetBlockedPayload,
    EventType.AGENT_ACCESS_DENIED: AgentAccessDeniedPayload,
    EventType.PLAN_DECIDED: PlanDecidedPayload,
    EventType.GIT_PUBLISHED: GitPublishedPayload,
    EventType.ERROR: ErrorPayload,
}
