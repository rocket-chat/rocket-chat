"""Session forking.

A fork branches a conversation without touching the original: it copies history up to an
optional turn, the task checklist and the bound agent, records its provenance in
``metadata.forked_from``, and gives the child its own workspace seeded from the parent
(never a shared volume). Later turns on either branch stay isolated because each has its
own conversation history and sandbox.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from agent_core.tools import git_tools
from config_engine.registry import DEFAULT_SANDBOX_IMAGE
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sandbox_driver.seeding import seed_workspace_from
from sandbox_driver.workspace import list_repositories
from sqlalchemy import delete

from api.auth.access import _check_session_access, _get_request_identity
from api.db.models import (
    SessionGoalModel,
    SessionWaitModel,
    SubagentTranscriptModel,
    TurnTraceModel,
)
from api.routers.sessions_list import (
    SessionListItem,
    _publish_session_updated,
    session_list_item,
)
from api.services.artifacts import get_artifact_store
from specifications.interfaces.sandbox import SandboxStatus, WorkspaceSpec
from specifications.interfaces.session import SessionArtifact, SessionRecord, SessionStoreProtocol

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Sessions"])


class ForkSessionRequest(BaseModel):
    """Payload for branching a session."""

    up_to_turn: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Copy history through the Nth user turn, inclusive of its assistant response. "
            "Omit to copy the entire conversation."
        ),
    )
    title: str | None = Field(default=None, description="Optional title for the fork")


class RewindSessionRequest(BaseModel):
    """Payload for rewinding a session to an earlier turn."""

    target_turn: int = Field(
        ...,
        ge=1,
        description="The turn index (1-indexed user turn) to rewind back to.",
    )


def truncate_history(history: list[Any], up_to_turn: int | None) -> list[Any]:
    """Return the leading history that belongs to the first ``up_to_turn`` user turns.

    The cut is made *before* the next user message, so a fork never starts mid-turn with
    an assistant answer whose question was left behind.
    """
    if up_to_turn is None:
        return list(history)
    copied: list[Any] = []
    seen_user_turns = 0
    for message in history:
        if getattr(message, "role", "") == "user":
            seen_user_turns += 1
            if seen_user_turns > up_to_turn:
                break
        copied.append(message)
    return copied


@router.post("/sessions/{session_id}/fork", response_model=SessionListItem)
async def fork_session(
    session_id: str,
    req: ForkSessionRequest,
    request: Request,
) -> SessionListItem:
    """Branch a session into a new one with its own history and workspace."""
    store: SessionStoreProtocol = request.app.state.session_store
    driver = getattr(request.app.state, "sandbox_driver", None)
    _, user_id, roles = _get_request_identity(request)

    parent = await store.get_session(session_id)
    if parent is None:
        raise HTTPException(status_code=404, detail="Session not found")
    # A fork creates a new owned session, so it is a write action: the read-only role
    # must not be able to branch a session it can only view.
    _check_session_access(parent, user_id, roles, require_write=True)

    child_id = f"session_{uuid.uuid4().hex[:10]}"
    excluded_metadata_keys = {
        "public_link",
        "public_link_token",
        "slack_channel_id",
        "slack_thread_ts",
        "slack_user_id",
        "slack_message_ts",
        "sandbox_id",
        "turn_lease_holder",
        "turn_lease_expires",
        "processes_reported",
        "active_wait",
    }
    raw_meta = dict(parent.metadata) if isinstance(parent.metadata, dict) else {}
    metadata: dict[str, Any] = {
        k: v for k, v in raw_meta.items() if k not in excluded_metadata_keys
    }
    # Provenance is rewritten, never inherited: a fork of a fork points at its direct
    # parent, and sharing/collaboration never silently carries over to the new owner.
    metadata["forked_from"] = {"session_id": parent.session_id, "turn": req.up_to_turn}
    metadata["is_shared"] = False
    metadata["collaborators"] = []

    record = SessionRecord(
        session_id=child_id,
        tenant_org_id=parent.tenant_org_id,
        tenant_user_id=user_id or parent.tenant_user_id,
        title=req.title or f"{parent.title} (fork)",
        source=parent.source,
        sandbox_status=SandboxStatus.NON_EXISTENT,
        container_image=parent.container_image,
        git_repo=parent.git_repo,
        git_branch=parent.git_branch,
        conversation_history=truncate_history(parent.conversation_history, req.up_to_turn),
        metadata=metadata,
        checklist=list(parent.checklist or []),
    )

    await _seed_child_workspace(driver, parent, record, metadata)
    await store.create_session(record)

    art_store = get_artifact_store(getattr(request.app.state, "db_manager", None))
    parent_artifacts = await art_store.list_artifacts(parent.session_id, parent.tenant_org_id)
    for art in parent_artifacts:
        new_art = SessionArtifact(
            id=f"art_{uuid.uuid4().hex[:12]}",
            session_id=child_id,
            tenant_org_id=parent.tenant_org_id,
            title=art.title,
            filename=art.filename,
            artifact_type=art.artifact_type,
            version=1,
            content=art.content,
            metadata=dict(art.metadata),
            created_at=int(time.time()),
            updated_at=int(time.time()),
        )
        await art_store.create_artifact(new_art)

    # The agent's own memory of the conversation (tool calls and results) follows the fork,
    # so the child's next model request carries the parent's context.
    transcripts = getattr(request.app.state, "transcript_store", None)
    if transcripts is not None:
        await transcripts.copy_prefix(
            parent.session_id, record.session_id, record.tenant_org_id, req.up_to_turn
        )
    await _publish_session_updated(request, record)
    return session_list_item(record)


async def _seed_child_workspace(
    driver: Any,
    parent: SessionRecord,
    record: SessionRecord,
    metadata: dict[str, Any],
) -> None:
    """Seed the fork's own workspace and record how it was produced.

    A seeding failure must not lose the fork — the conversation is the valuable part — so
    it degrades to an unseeded workspace with an explicit origin marker rather than
    aborting the request.
    """
    if driver is None:
        metadata["workspace_origin"] = "none"
        return
    spec = WorkspaceSpec(
        session_id=record.session_id,
        tenant_org_id=record.tenant_org_id,
        tenant_user_id=record.tenant_user_id,
        container_image=parent.container_image or DEFAULT_SANDBOX_IMAGE,
    )
    try:
        seed = await seed_workspace_from(driver, parent.session_id, record.session_id, spec)
    except Exception:
        logger.exception("Fork %s: workspace seeding failed", record.session_id)
        metadata["workspace_origin"] = "none"
        return
    metadata["workspace_origin"] = seed.origin
    if seed.file_count > 0:
        # The child volume exists but no compute is running for it yet.
        record.sandbox_status = SandboxStatus.HIBERNATED


async def _cleanup_stores_on_rewind(
    request: Request, session_id: str, org_id: str, target_turn: int
) -> None:
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is not None and hasattr(orchestrator, "_session_histories"):
        orchestrator._session_histories.pop(session_id, None)

    compaction_store = getattr(request.app.state, "compaction_store", None)
    if compaction_store is not None:
        try:
            await compaction_store.delete_compactions(session_id, org_id)
        except Exception:
            logger.exception("Failed to delete compactions on rewind for %s", session_id)

    transcripts = getattr(request.app.state, "transcript_store", None)
    if transcripts is not None and hasattr(transcripts, "truncate_to_turn"):
        try:
            await transcripts.truncate_to_turn(session_id, org_id, target_turn)
        except Exception:
            logger.exception("Failed to truncate transcript to turn %s", target_turn)

    art_store = get_artifact_store(getattr(request.app.state, "db_manager", None))
    try:
        await art_store.delete_artifacts(session_id, org_id)
    except Exception:
        logger.exception("Failed to reset artifacts on rewind for %s", session_id)


async def _clear_db_models_on_rewind(
    db_mgr: Any, session_id: str, org_id: str, target_turn: int
) -> None:
    if db_mgr is None or not getattr(db_mgr, "session_factory", None):
        return
    try:
        async with db_mgr.session(tenant_org_id=org_id) as db_sess:
            await db_sess.execute(
                delete(TurnTraceModel).where(
                    TurnTraceModel.session_id == session_id,
                    TurnTraceModel.turn_index > target_turn,
                )
            )
            await db_sess.execute(
                delete(SessionGoalModel).where(SessionGoalModel.session_id == session_id)
            )
            await db_sess.execute(
                delete(SubagentTranscriptModel).where(
                    SubagentTranscriptModel.session_id == session_id
                )
            )
            await db_sess.execute(
                delete(SessionWaitModel).where(SessionWaitModel.session_id == session_id)
            )
            await db_sess.commit()
    except Exception:
        logger.exception("Failed cleaning DB records on rewind for %s", session_id)


async def _revert_workspace_git(driver: Any, session_id: str, target_turn: int) -> None:
    if driver is None:
        return
    try:
        status = await driver.get_status(session_id)
        if status != SandboxStatus.RUNNING:
            return
        all_repos = await list_repositories(driver, session_id)
        for repo_dir in all_repos:
            try:
                checkpoints = await git_tools.list_checkpoints(driver, session_id, repo_dir)
                matched = next(
                    (
                        c
                        for c in checkpoints
                        if c.name == f"rocket-checkpoint-{target_turn}"
                        or c.name.startswith(f"rocket-checkpoint-{target_turn}-")
                    ),
                    None,
                )
                if matched:
                    await git_tools.git_revert(driver, session_id, matched.name, "hard", repo_dir)
                await driver.exec_command(
                    session_id, "git clean -fd", workdir=repo_dir, timeout_seconds=15
                )
            except Exception:
                logger.exception("Rewind: failed to revert workspace git in %s", repo_dir)
    except Exception:
        logger.exception("Rewind: failed checking driver status")


@router.post("/sessions/{session_id}/rewind", response_model=SessionListItem)
async def rewind_session(
    session_id: str,
    req: RewindSessionRequest,
    request: Request,
) -> SessionListItem:
    """Time-travel rewind: roll conversation history, transcript, and sandbox back to turn N."""
    store: SessionStoreProtocol = request.app.state.session_store
    driver = getattr(request.app.state, "sandbox_driver", None)
    _, user_id, roles = _get_request_identity(request)

    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(session, user_id, roles, require_write=True)

    session_locks = getattr(request.app.state, "session_write_locks", None)
    lock = session_locks.lock(session_id) if session_locks is not None else None
    if lock is not None and lock.locked():
        raise HTTPException(
            status_code=409,
            detail="Cannot rewind session while a turn is currently executing. Cancel it first.",
        )

    async def _do_rewind() -> SessionListItem:
        def _mutate(record: SessionRecord) -> None:
            record.conversation_history = truncate_history(
                record.conversation_history, req.target_turn
            )
            if isinstance(record.metadata, dict):
                record.metadata.pop("plan_md", None)
                record.metadata.pop("progress_md", None)
            record.checklist = []

        updated = await store.mutate_session(session_id, session.tenant_org_id, _mutate)
        if updated is None:
            raise HTTPException(status_code=500, detail="Failed to update session")

        await _cleanup_stores_on_rewind(request, session_id, session.tenant_org_id, req.target_turn)
        await _clear_db_models_on_rewind(
            getattr(request.app.state, "db_manager", None),
            session_id,
            session.tenant_org_id,
            req.target_turn,
        )
        await _revert_workspace_git(driver, session_id, req.target_turn)
        await _publish_session_updated(request, updated)
        return session_list_item(updated)

    if lock is not None:
        async with lock:
            return await _do_rewind()
    return await _do_rewind()
