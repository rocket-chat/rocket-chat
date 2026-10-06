"""
Interface definitions for Agent Workflow & Sub-Agent Orchestration.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


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
        whitelisted_tools: list[str] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """
        Executes an agent reasoning & action cycle.
        Streams agent events (status, thoughts, tool calls, diffs).
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
