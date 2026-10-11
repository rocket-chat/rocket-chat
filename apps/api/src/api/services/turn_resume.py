"""Resume turns that a restart interrupted.

A deploy, a node drain or a crash used to end a running task with an ``interrupted`` marker and
leave the user to notice and re-send. The conversation (including finished tool results) is
durable, and the sandbox outlives the API process, so the task can simply continue: the
replica that finds the interrupted turn claims it atomically and runs one more turn that tells
the agent what happened.

Bounded on purpose: a task is resumed at most ``ROCKET_TURN_AUTO_RESUME_ATTEMPTS`` times
(a turn that keeps killing its process stops looping), only soon after the interruption, never
when the user has since written something else, and never when the organization has turned
``governance.auto_resume_turns`` off.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Awaitable, Callable

from api.db.session import DatabaseSessionManager
from api.db.store import ResumableTurn, TurnTraceStore
from api.services.settings import load_org_config
from api.services.turn_trace import INTERRUPTED_TURN_ERROR, PROCESS_INSTANCE_ID

logger = logging.getLogger(__name__)

DEFAULT_AUTO_RESUME_ATTEMPTS = 1
# Past this the user has probably moved on and the workspace may have been reaped.
RESUME_WINDOW_SECONDS = 15 * 60

RESUME_NOTICE = (
    "The server running your previous work restarted before it finished. The conversation and "
    "the workspace are as you left them. Review what you already did (a tool that was running "
    "has no result), then continue the original task without repeating work that succeeded."
)

ResumeTurn = Callable[[ResumableTurn], Awaitable[None]]


def auto_resume_attempts() -> int:
    raw = os.getenv("ROCKET_TURN_AUTO_RESUME_ATTEMPTS", "").strip()
    return int(raw) if raw.isdigit() else DEFAULT_AUTO_RESUME_ATTEMPTS


async def _org_allows_resume(db_manager: DatabaseSessionManager, org_id: str) -> bool:
    settings, _ = await load_org_config(db_manager, org_id, "governance")
    return bool(settings.get("auto_resume_turns", True))


async def resume_interrupted_turns(
    admin_manager: DatabaseSessionManager | None,
    app_manager: DatabaseSessionManager | None,
    resume: ResumeTurn,
) -> int:
    """Claim interrupted turns and hand each to ``resume``; returns how many were resumed.

    ``admin_manager`` (the schema owner) sees every tenant's rows to make the claim;
    ``app_manager`` reads each organization's settings under its own tenant context.
    """
    attempts = auto_resume_attempts()
    if admin_manager is None or attempts < 1:
        return 0
    claimed = await TurnTraceStore(admin_manager).claim_resumable(
        PROCESS_INSTANCE_ID,
        max_attempts=attempts,
        interrupted_after=int(time.time()) - RESUME_WINDOW_SECONDS,
        error=INTERRUPTED_TURN_ERROR,
    )
    resumed = 0
    for turn in claimed:
        if app_manager is not None and not await _org_allows_resume(app_manager, turn.org_id):
            logger.info(
                "Organization %s disabled auto-resume; leaving %s interrupted",
                turn.org_id,
                turn.trace_id,
            )
            continue
        try:
            await resume(turn)
            resumed += 1
        except Exception:
            logger.exception("Could not resume interrupted turn %s", turn.trace_id)
    if resumed:
        logger.warning("Resumed %d interrupted turn(s)", resumed)
    return resumed
