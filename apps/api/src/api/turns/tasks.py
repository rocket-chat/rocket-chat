"""Starting, guarding and resuming turns as background tasks."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from fastapi import (
    FastAPI,
)
from git_engine.engine import GitEngine

from api.db.session import DatabaseSessionManager, current_tenant_org_id
from api.db.store import ResumableTurn
from api.services.attachments import AttachmentService
from api.services.metrics import GlobalConcurrencyGate, SessionWriteLocks
from api.services.missions import Mission
from api.services.repository_hint import with_repository_hint
from api.services.title import TITLE_SOURCE_USER
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
    SandboxStatus,
)
from specifications.interfaces.session import (
    SessionRecord,
    SessionSource,
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
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def launch_mission(app: FastAPI, mission: Mission) -> str:
    """Start one run of a scheduled mission as an ordinary session owned by its creator."""
    state = app.state
    session_id = f"session_{uuid.uuid4().hex[:10]}"
    metadata: dict[str, Any] = {
        "mission_id": mission.id,
        "is_shared": False,
        "collaborators": [],
        "title_source": TITLE_SOURCE_USER,
    }
    if mission.agent_id:
        metadata["agent_id"] = mission.agent_id
    if mission.permission_mode:
        metadata["permission_mode"] = mission.permission_mode
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    token = current_tenant_org_id.set(mission.org_id)
    try:
        await state.session_store.create_session(
            SessionRecord(
                session_id=session_id,
                tenant_org_id=mission.org_id,
                tenant_user_id=mission.user_id,
                title=f"{mission.name} · {stamp} UTC",
                source=SessionSource.SCHEDULED,
                sandbox_status=SandboxStatus.NON_EXISTENT,
                metadata=metadata,
            )
        )
        turn = _spawn_background_turn(
            session_id=session_id,
            prompt=with_repository_hint(mission.prompt, mission.git_repo, mission.git_branch),
            orchestrator=state.orchestrator,
            event_bus=state.event_bus,
            driver=getattr(state, "sandbox_driver", None),
            registry=getattr(state, "tool_registry", None),
            store=state.session_store,
            git_engine=getattr(state, "git_engine", None),
            db_mgr=getattr(state, "db_manager", None),
            author_user_id=mission.user_id,
            attachment_service=getattr(state, "attachment_service", None),
            concurrency_gate=getattr(state, "global_concurrency_gate", None),
            session_locks=getattr(state, "session_write_locks", None),
        )
        if mission.slack_channel:
            turn.add_done_callback(lambda _done: _report_mission_to_slack(app, mission, session_id))
    finally:
        current_tenant_org_id.reset(token)
    return session_id


def _report_mission_to_slack(app: FastAPI, mission: Mission, session_id: str) -> None:
    """When a run ends, tell the channel its owner chose, through one of the organization's
    bots (an organization without a bot is not an error)."""
    manager = getattr(app.state, "slack_bot_manager", None)
    bots = manager.for_org(mission.org_id) if manager is not None else []
    if not bots or not mission.slack_channel:
        return
    assistant = bots[0]

    async def report() -> None:
        token = current_tenant_org_id.set(mission.org_id)
        try:
            record = await app.state.session_store.get_session(session_id)
            answers = [
                m for m in (record.conversation_history if record else []) if m.role == "assistant"
            ]
            answer = answers[-1].content or "" if answers else ""
            await assistant.post_mission_report(
                org_id=mission.org_id,
                channel_id=str(mission.slack_channel),
                mission_name=mission.name,
                session_id=session_id,
                answer=answer,
                succeeded=bool(answers) and "Mission Error" not in answer[:40],
            )
        except Exception:
            logger.exception("Could not report mission %s to Slack", mission.id)
        finally:
            current_tenant_org_id.reset(token)

    task = asyncio.create_task(report())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def start_turn_on_session(
    app: FastAPI,
    org_id: str,
    session_id: str,
    prompt: str,
    *,
    resume_attempt: int = 0,
    synthetic_prompt: bool = False,
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
        )
    finally:
        current_tenant_org_id.reset(token)
    return True


async def resume_claimed_turn(app: FastAPI, turn: ResumableTurn) -> None:
    """Start the follow-up turn for an interrupted turn this replica has claimed."""
    await start_turn_on_session(
        app,
        turn.org_id,
        turn.session_id,
        RESUME_NOTICE,
        resume_attempt=turn.resume_attempt + 1,
        synthetic_prompt=True,
    )
