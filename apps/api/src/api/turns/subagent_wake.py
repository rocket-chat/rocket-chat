"""Waking a session when the background subagent it waits for has finished."""

from __future__ import annotations

import asyncio
import logging

from agent_core.subagent_report import render_for_parent
from agent_core.subagents import result_from_dict

from api.services.subagent_runs import get_subagent_run_store
from api.turns.context import TurnContext
from api.turns.shared import _background_tasks
from specifications.interfaces.agent import SUBAGENT_RUN_ACTIVE
from specifications.interfaces.events import EventType, SessionEvent

logger = logging.getLogger(__name__)

POLL_SECONDS = 3.0


async def _settled_report(ctx: TurnContext, subagent_id: str, max_wait: float) -> str | None:
    """The child's rendered report once its run ends; None when the wait timed out."""
    store = get_subagent_run_store(ctx.req.db_mgr)
    org = ctx.session.tenant_org_id
    deadline = asyncio.get_running_loop().time() + max_wait
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(POLL_SECONDS)
        try:
            run = await store.get_run(org, ctx.session_id, subagent_id)
        except Exception:
            logger.warning("Could not read subagent run %s", subagent_id, exc_info=True)
            continue
        if run is None:
            return f"There is no subagent {subagent_id} in this session."
        if run.status not in SUBAGENT_RUN_ACTIVE:
            if run.result is None:
                return f"Subagent {subagent_id} ended ({run.status}) without a result."
            return render_for_parent(result_from_dict(run.result))
    return None


def schedule_subagent_wake(
    ctx: TurnContext, subagent_id: str, timeout_seconds: int, wait_id: str
) -> None:
    async def _wake() -> None:
        report = await _settled_report(ctx, subagent_id, float(timeout_seconds))
        status = "completed" if report is not None else "timeout"
        summary = (
            f"Subagent {subagent_id} finished."
            if report is not None
            else f"Subagent {subagent_id} is still running after {timeout_seconds}s."
        )
        await ctx.req.event_bus.publish(
            SessionEvent(
                session_id=ctx.session_id,
                event_type=EventType.WAIT_COMPLETED,
                payload={
                    "wait_id": wait_id,
                    "wait_type": "subagent",
                    "target": subagent_id,
                    "status": status,
                    "summary": summary,
                },
            )
        )
        from api.turns.tasks import _spawn_background_turn

        _spawn_background_turn(
            session_id=ctx.session_id,
            prompt=f"{summary} Resuming execution.\n\n{report or ''}".strip(),
            orchestrator=ctx.req.orchestrator,
            event_bus=ctx.req.event_bus,
            driver=ctx.req.driver,
            registry=ctx.req.registry,
            store=ctx.req.store,
            model=ctx.effective_model,
            git_engine=ctx.req.git_engine,
            db_mgr=ctx.req.db_mgr,
            concurrency_gate=ctx.req.concurrency_gate,
            session_locks=ctx.req.session_locks,
            synthetic_prompt=True,
            overlay=ctx.req.overlay,
        )

    task = asyncio.create_task(_wake())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
