"""The data one turn carries: what was asked, what it accumulates, and what it resolved.

``TurnRequest`` is immutable input. ``TurnState`` is the mutable bookkeeping that must be
readable from the ``finally`` of a turn even when it failed half way. ``TurnContext`` exists
only once the turn has been planned and holds everything the phases and event handlers need.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from config_engine.cascading import ModelRouter
from git_engine.engine import GitEngine

from api.db.session import DatabaseSessionManager
from api.services.attachments import AttachmentService
from api.services.audit import AuditService, GovernancePolicy
from api.services.mcp_runtime import McpToolProvider
from api.services.metrics import GlobalConcurrencyGate, SessionWriteLocks
from api.services.redaction import Redactor
from api.services.turn_trace import TurnTraceAccumulator
from specifications.interfaces.agent import AgentBinding, TurnOverlay
from specifications.interfaces.events import EventBusProtocol
from specifications.interfaces.sandbox import SandboxDriverProtocol
from specifications.interfaces.session import SessionRecord, SessionStoreProtocol


@dataclass(frozen=True)
class TurnSelection:
    """What the person chose for this turn in the composer: an agent, or a raw model.

    ``raw`` runs no agent at all (generic prompt, every tool loaded) on the turn's model;
    otherwise ``agent_id`` names the agent, falling back to the session's.
    """

    agent_id: str | None = None
    raw: bool = False


@dataclass(frozen=True)
class TurnRequest:
    session_id: str
    prompt: str
    orchestrator: AsyncReActOrchestrator
    event_bus: EventBusProtocol
    driver: SandboxDriverProtocol | None
    registry: ToolRegistry | None
    store: SessionStoreProtocol
    model: str | None = None
    git_engine: GitEngine | None = None
    db_mgr: DatabaseSessionManager | None = None
    message_id: str | None = None
    author_user_id: str | None = None
    attachment_service: AttachmentService | None = None
    attachment_ids: list[str] | None = None
    overlay: TurnOverlay | None = None
    resume_attempt: int = 0
    selection: TurnSelection | None = None
    synthetic_prompt: bool = False
    concurrency_gate: GlobalConcurrencyGate | None = None
    session_locks: SessionWriteLocks | None = None


@dataclass
class TurnState:
    """Everything a turn accumulates; read by the ``finally`` even after a failure."""

    # TTFT instrumentation: the first non-reasoning token marks the
    # conversational end of prefill; a turn that ran any tool counts as sandbox-class.
    started_at: float = field(default_factory=time.monotonic)
    first_token_at: float | None = None
    assistant_parts: list[str] = field(default_factory=list)
    reasoning_parts: list[str] = field(default_factory=list)
    executed_tools: list[dict[str, Any]] = field(default_factory=list)
    # Reasoning already saved with the part of the turn before a message the person sent
    # during it (see ``TurnEventHandlers._user_steer``); kept for the turn's trace.
    earlier_reasoning: list[str] = field(default_factory=list)
    # How many of ``reasoning_parts`` are saved with the tool call they led to (the rest is
    # the reasoning after the turn's last call, saved on the answer).
    reasoning_attached: int = 0
    had_post_failure_synthesis: bool = False
    synthesis_turns: int = 30
    trace: TurnTraceAccumulator | None = None
    mcp_tools: McpToolProvider | None = None
    turn_error: BaseException | None = None
    # The question the turn is paused on, until the next event shows it was answered.
    open_decision: dict[str, Any] | None = None
    orchestrator_error: str | None = None
    # Resolved while planning; pre-declared so the `finally` (summary, title) can reason
    # about them even when routing fails before assignment.
    effective_model: str | None = None
    router: ModelRouter | None = None
    is_continuation: bool = False
    is_waiting: bool = False
    wait_target: str | None = None
    wait_summary: str | None = None


@dataclass
class TurnContext:
    """A planned turn: the request, its state and the policies resolved for it."""

    req: TurnRequest
    state: TurnState
    session: SessionRecord
    trace: TurnTraceAccumulator
    binding: AgentBinding
    governance: GovernancePolicy
    redactor: Redactor
    audit: AuditService
    effective_model: str
    routing_reason: str
    effective_max_turns: int

    @property
    def session_id(self) -> str:
        return self.req.session_id
