"""The payload of every session event, as a typed contract.

``SessionEvent.payload`` is a plain dict on the wire, which is easy to drift: a publisher adds a
key, a client reads another. These ``TypedDict`` definitions are the reviewed shape of each
event type. The in-process event bus validates every published event against them when
``ROCKET_VALIDATE_EVENTS=1`` (the test suite sets it), so a payload change fails the tests
that exercise it until the contract and the WebSocket protocol documentation are updated.

Keys marked ``NotRequired`` are optional on the wire; unknown keys are rejected.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from specifications.interfaces.events import EventType


class TokenPayload(TypedDict):
    delta: str


class StatusPayload(TypedDict):
    """Progress of a turn. ``final`` marks the runner's closing event (answer stored)."""

    status: NotRequired[str]
    state: NotRequired[str]
    message: NotRequired[str]
    error: NotRequired[str]
    final: NotRequired[bool]
    action_id: NotRequired[str]
    question_id: NotRequired[str]
    sandbox_status: NotRequired[str]
    turns_taken: NotRequired[int]
    max_turns: NotRequired[int]
    reason: NotRequired[str]


class ToolStartedPayload(TypedDict):
    id: NotRequired[str | None]
    name: str
    arguments: dict[str, Any]


class ToolCompletedPayload(TypedDict):
    id: NotRequired[str | None]
    name: str
    arguments: dict[str, Any]
    result: Any
    status: str
    duration_ms: NotRequired[int | float]
    redacted: NotRequired[int]


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


class LogChunkPayload(TypedDict):
    text: str


class SessionUpdatedPayload(TypedDict):
    title: NotRequired[str]
    session_id: NotRequired[str]
    is_shared: NotRequired[bool]
    collaborators: NotRequired[list[str]]
    is_pinned: NotRequired[bool]
    is_archived: NotRequired[bool]


class UserMessagePayload(TypedDict):
    message_id: str
    role: str
    content: str
    author_user_id: str | None


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


class SubagentProgressPayload(TypedDict):
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
    status: NotRequired[str]


class PostFailureSynthesisPayload(TypedDict):
    reason: str
    error: NotRequired[str]
    max_turns: NotRequired[int]
    turns_taken: NotRequired[int]


class ErrorPayload(TypedDict):
    error: str


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
    EventType.LOG_CHUNK: LogChunkPayload,
    EventType.SANDBOX_STATE: SandboxStatePayload,
    EventType.SESSION_UPDATED: SessionUpdatedPayload,
    EventType.USER_MESSAGE: UserMessagePayload,
    EventType.PLAN_UPDATED: PlanUpdatedPayload,
    EventType.FOLLOWUP_SUGGESTION: FollowupSuggestionPayload,
    EventType.SUBAGENT_STARTED: SubagentStartedPayload,
    EventType.SUBAGENT_PROGRESS: SubagentProgressPayload,
    EventType.SUBAGENT_COMPLETED: SubagentCompletedPayload,
    EventType.POST_FAILURE_SYNTHESIS: PostFailureSynthesisPayload,
    EventType.PROCESS_STARTED: ProcessPayload,
    EventType.PROCESS_EXITED: ProcessPayload,
    EventType.GITHUB_EVENT: GithubEventPayload,
    EventType.ERROR: ErrorPayload,
}
