"""Starting, guarding and resuming turns as background tasks."""

import asyncio
import logging

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from fastapi import (
    FastAPI,
)
from git_engine.engine import GitEngine

from api.db.session import DatabaseSessionManager, current_tenant_org_id
from api.db.store import ResumableTurn, TurnTraceStore
from api.services.attachments import AttachmentService
from api.services.interrupted_work import build_interrupted_work, resume_notice
from api.services.metrics import GlobalConcurrencyGate, SessionWriteLocks
from api.services.queued_messages import QueuedMessageStore
from api.services.turn_resume import RESUME_NOTICE
from api.turns.context import TurnSelection
from api.turns.runner import execute_turn_background
from api.turns.shared import _background_tasks
from specifications.interfaces.agent import (
    TurnOverlay,
)
from specifications.interfaces.events import EventBusProtocol
from specifications.interfaces.sandbox import (
    SandboxDriverProtocol,
)
from specifications.interfaces.session import (
    SessionStoreProtocol,
)

logger = logging.getLogger(__name__)


async def run_guarded_turn(
    session_id: str,
    prompt: str,
    orchestrator: AsyncReActOrchestrator,
    event_bus: EventBusProtocol,
    driver: SandboxDriverProtocol | None,
    registry: ToolRegistry | None,
    store: SessionStoreProtocol,
    model: str | None = None,
    git_engine: GitEngine | None = None,
    db_mgr: DatabaseSessionManager | None = None,
    message_id: str | None = None,
    author_user_id: str | None = None,
    attachment_service: AttachmentService | None = None,
    attachment_ids: list[str] | None = None,
    concurrency_gate: GlobalConcurrencyGate | None = None,
    session_locks: SessionWriteLocks | None = None,
    overlay: TurnOverlay | None = None,
    resume_attempt: int = 0,
    selection: TurnSelection | None = None,
    synthetic_prompt: bool = False,
    reasoning_effort: str | None = None,
) -> None:
    """Run one turn under process-wide backpressure and a per-session single-writer lock.

    The shared entry point for every transport (web, Slack, webhooks): the lock makes a
    second message in the same session wait for the first instead of racing it. Without an
    injected gate (unit tests, in-memory store) the turn runs unguarded.
    """

    async def _execute() -> None:
        await execute_turn_background(
            session_id=session_id,
            prompt=prompt,
            orchestrator=orchestrator,
            event_bus=event_bus,
            driver=driver,
            registry=registry,
            store=store,
            model=model,
            git_engine=git_engine,
            db_mgr=db_mgr,
            message_id=message_id,
            author_user_id=author_user_id,
            attachment_service=attachment_service,
            attachment_ids=attachment_ids,
            overlay=overlay,
            resume_attempt=resume_attempt,
            selection=selection,
            synthetic_prompt=synthetic_prompt,
            concurrency_gate=concurrency_gate,
            session_locks=session_locks,
            reasoning_effort=reasoning_effort,
        )

    if concurrency_gate is not None and session_locks is not None:
        async with concurrency_gate.slot():
            async with session_locks.lock(session_id):
                await _execute()
        return
    await _execute()


def _spawn_background_turn(
    session_id: str,
    prompt: str,
    orchestrator: AsyncReActOrchestrator,
    event_bus: EventBusProtocol,
    driver: SandboxDriverProtocol | None,
    registry: ToolRegistry | None,
    store: SessionStoreProtocol,
    model: str | None = None,
    git_engine: GitEngine | None = None,
    db_mgr: DatabaseSessionManager | None = None,
    message_id: str | None = None,
    author_user_id: str | None = None,
    attachment_service: AttachmentService | None = None,
    attachment_ids: list[str] | None = None,
    concurrency_gate: GlobalConcurrencyGate | None = None,
    session_locks: SessionWriteLocks | None = None,
    overlay: TurnOverlay | None = None,
    resume_attempt: int = 0,
    selection: TurnSelection | None = None,
    synthetic_prompt: bool = False,
    reasoning_effort: str | None = None,
) -> asyncio.Task[None]:
    task = asyncio.create_task(
        run_guarded_turn(
            session_id=session_id,
            prompt=prompt,
            orchestrator=orchestrator,
            event_bus=event_bus,
            driver=driver,
            registry=registry,
            store=store,
            model=model,
            git_engine=git_engine,
            db_mgr=db_mgr,
            message_id=message_id,
            author_user_id=author_user_id,
            attachment_service=attachment_service,
            attachment_ids=attachment_ids,
            concurrency_gate=concurrency_gate,
            session_locks=session_locks,
            overlay=overlay,
            resume_attempt=resume_attempt,
            selection=selection,
            synthetic_prompt=synthetic_prompt,
            reasoning_effort=reasoning_effort,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def start_turn_on_session(
    app: FastAPI,
    org_id: str,
    session_id: str,
    prompt: str,
    *,
    resume_attempt: int = 0,
    synthetic_prompt: bool = False,
    message_id: str | None = None,
    author_user_id: str | None = None,
) -> bool:
    """Start a turn on an existing session that nobody typed into; False when it is gone."""
    state = app.state
    token = current_tenant_org_id.set(org_id)
    try:
        if await state.session_store.get_session(session_id) is None:
            return False
        _spawn_background_turn(
            session_id=session_id,
            prompt=prompt,
            orchestrator=state.orchestrator,
            event_bus=state.event_bus,
            driver=getattr(state, "sandbox_driver", None),
            registry=getattr(state, "tool_registry", None),
            store=state.session_store,
            git_engine=getattr(state, "git_engine", None),
            db_mgr=getattr(state, "db_manager", None),
            attachment_service=getattr(state, "attachment_service", None),
            concurrency_gate=getattr(state, "global_concurrency_gate", None),
            session_locks=getattr(state, "session_write_locks", None),
            resume_attempt=resume_attempt,
            synthetic_prompt=synthetic_prompt,
            message_id=message_id,
            author_user_id=author_user_id,
        )
    finally:
        current_tenant_org_id.reset(token)
    return True


async def start_queued_turns(app: FastAPI, org_id: str, session_id: str) -> int:
    """Start, in order, the turns for the messages of ``session_id`` that no turn took.

    For a message whose replica restarted (the sweeper); a turn that ends here starts them
    itself (``TurnRunner._start_queued_turns``).
    """
    store = QueuedMessageStore(getattr(app.state, "db_manager", None))
    token = current_tenant_org_id.set(org_id)
    try:
        claimed = await store.claim_for_next_turn(org_id, session_id)
    finally:
        current_tenant_org_id.reset(token)
    for message in claimed:
        await start_turn_on_session(
            app,
            org_id,
            session_id,
            message.content,
            message_id=message.id,
            author_user_id=message.author_user_id,
        )
    return len(claimed)


async def _resume_instruction(app: FastAPI, turn: ResumableTurn) -> str:
    """The instruction for the follow-up turn: where the interrupted one stopped, when known."""
    try:
        db_manager = getattr(app.state, "db_manager", None)
        transcripts = getattr(app.state, "transcript_store", None)
        if db_manager is None or transcripts is None:
            return RESUME_NOTICE
        trace = await TurnTraceStore(db_manager).get(turn.trace_id, turn.org_id)
        if trace is None:
            return RESUME_NOTICE
        messages = await transcripts.load(turn.session_id, turn.org_id)
        return resume_notice(RESUME_NOTICE, build_interrupted_work(trace, messages))
    except Exception:
        logger.exception("Could not describe where turn %s stopped", turn.trace_id)
        return RESUME_NOTICE


async def resume_claimed_turn(app: FastAPI, turn: ResumableTurn) -> None:
    """Start the follow-up turn for an interrupted turn this replica has claimed."""
    await start_turn_on_session(
        app,
        turn.org_id,
        turn.session_id,
        await _resume_instruction(app, turn),
        resume_attempt=turn.resume_attempt + 1,
        synthetic_prompt=True,
    )
