"""Running a mission: who the run acts as, its session and prompt, and what happens when it ends.

Every source ends in ``start_run``: a schedule, an event a trigger took, the mission's own
webhook, or a person's *Run now*. A run about an item (a pull request, an issue) reuses that
item's session for this mission, so a follow-up continues the same conversation and a review
of new commits reviews only them; any other run gets a fresh session. The turn runs in the
background; when it ends, ``complete_run`` hands its result to the deliveries
(``mission_delivery``).
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any

from fastapi import FastAPI
from sandbox_driver.workspace import RepositoryNotFoundError, resolve_repository_dir
from sqlalchemy import select

from api.db.models import TeamMembershipModel, UserModel
from api.db.session import current_tenant_org_id
from api.db.store import ensure_tenant_identity
from api.services.github_watches import AUTO_FIX_KEY
from api.services.memberships import ensure_membership
from api.services.mission_delivery import complete_run_soon, mission_store
from api.services.mission_records import (
    MISSION_ID_KEY,
    MISSION_TOOLSET,
    OUTPUT_KEY,
    REVIEWED_SHA_KEY,
    RUN_ID_KEY,
    SCHEMA_KEY,
    RunStore,
)
from api.services.missions import MissionStore, service_member_id
from api.services.repository_hint import with_repository_hint
from api.services.session_support import active_repository_of
from api.turns.tasks import _spawn_background_turn
from specifications.interfaces.automation import (
    OWN_SOURCES,
    REVIEW_SOURCES,
    Mission,
    MissionRun,
    TriggerEvent,
    delivery_schema,
)
from specifications.interfaces.sandbox import SandboxDriverProtocol, SandboxStatus
from specifications.interfaces.session import SessionRecord, SessionSource

logger = logging.getLogger(__name__)

# The built-in reviewer answers a review mission that names no agent.
REVIEWER_AGENT_ID = "persona-reviewer"
# The event text an agent reads is the event's own (an issue body, a comment): bounded.
MAX_EVENT_BODY_CHARS = 8000
SYNC_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class RunOutcome:
    status: str  # created, resumed, ignored, duplicate
    session_id: str = ""
    mission_id: str = ""
    run_id: str = ""
    reason: str = ""


# -- who a run is ----------------------------------------------------------------------------


async def run_identity(app: FastAPI, mission: Mission, fallback: str) -> str:
    """The member a run acts as: a personal mission's owner; a shared mission's own service
    member (made once, a member of the organization and of its team); ``fallback`` for the
    organization's built-in missions that are not stored (the event's author, as before)."""
    if mission.scope == "user" and mission.user_id:
        return mission.user_id
    if mission.id.startswith("default_"):
        return fallback
    member = mission.run_as or service_member_id(mission.id)
    db_mgr = getattr(app.state, "db_manager", None)
    if db_mgr is not None and getattr(db_mgr, "session_factory", None) is not None:
        async with db_mgr.session(tenant_org_id=mission.org_id) as session:
            await _ensure_service_member(session, mission, member)
    if mission.run_as != member:
        store: MissionStore = mission_store(app)
        stored = await store.get(mission.org_id, mission.id)
        if stored is not None:
            await store.save(replace(stored, run_as=member))
    return member


async def _ensure_service_member(session: Any, mission: Mission, member: str) -> None:
    if await session.get(UserModel, member) is None:
        session.add(
            UserModel(
                id=member,
                org_id=mission.org_id,
                email=f"{member}@missions.invalid",
                name=f"Mission: {mission.name}"[:255],
                created_at=int(time.time()),
            )
        )
        await session.flush()
    await ensure_membership(session, member, mission.org_id, "member")
    if mission.team_id:
        exists = (
            await session.execute(
                select(TeamMembershipModel.id).where(
                    TeamMembershipModel.team_id == mission.team_id,
                    TeamMembershipModel.user_id == member,
                )
            )
        ).first()
        if exists is None:
            session.add(
                TeamMembershipModel(
                    id=f"tm_{uuid.uuid4().hex[:16]}",
                    org_id=mission.org_id,
                    team_id=mission.team_id,
                    user_id=member,
                    role="member",
                    created_at=int(time.time()),
                )
            )


# -- sessions and prompts --------------------------------------------------------------------


# The keys sessions had before missions (built-in label missions, and missions carried over from
# triggers, whose ids start with ``trg_``): a follow-up on an item started then still continues
# its session.
_LEGACY_PREFIXES = {
    "github_issue_label": "gh",
    "github_issue_comment": "gh",
    "github_pr": "gh",
    "github_pr_review_comment": "gh",
    "github_workflow_failed": "ci",
    "gitlab_issue_label": "gl",
    "gitlab_mr": "gl",
    "gitlab_note": "gl",
    "gitlab_pipeline_failed": "ci",
    "jira_issue": "jira",
    "linear_issue": "linear",
}


def session_id_for(mission: Mission, event: TriggerEvent) -> str:
    """One session per item and mission (a review and a fix of one pull request are two
    missions, so two sessions); a run about no item gets a session of its own."""
    item = str(event.number) if event.number else event.key
    if not item or event.source in OWN_SOURCES:
        return f"session_{uuid.uuid4().hex[:10]}"
    scoped = f"{event.repository}_{item}" if event.repository else item
    slug = re.sub(r"[^a-z0-9_]+", "_", scoped.lower()).strip("_")
    if mission.id.startswith(("default_", "trg_")):
        prefix = "review" if mission.mode == "review" else _LEGACY_PREFIXES.get(event.source, "trg")
        return f"{prefix}_{slug}"[:64]
    short = mission.id.removeprefix("mission_")[:8]
    return f"m{short}_{slug}"[:64]


def _task(mission: Mission, event: TriggerEvent, follow_up: bool, reviewed_sha: str) -> str:
    body = event.body[:MAX_EVENT_BODY_CHARS]
    where = (
        f"{event.repository}#{event.number}" if event.number else (event.key or event.repository)
    )
    if mission.mode == "review":
        return review_prompt(event, reviewed_sha)
    if event.source in OWN_SOURCES:
        return body
    if follow_up:
        return f"Follow-up from @{event.actor} on {where}:\n\n{body}"
    if event.source in ("github_workflow_failed", "gitlab_pipeline_failed"):
        return (
            f"CI failed: {event.title}. Find the cause in the run's logs ({event.url}), fix it on "
            f"branch `{event.branch}` and push the fix.\n\n{body}"
        ).strip()
    branch = event.branch or f"agent/{(event.key or str(event.number)).lower()}-fix"
    return (
        f"Resolve {where}: {event.title}\n\n{body}\n\nDo the work on a branch named "
        f"`{branch}` and open a pull request that references it."
    )


def review_prompt(event: TriggerEvent, reviewed_sha: str) -> str:
    """Review the pull request (only the commits since ``reviewed_sha`` when it was reviewed)."""
    scope = (
        f"Review only the changes since the last review: `git diff {reviewed_sha}..{event.head_sha}`."
        if reviewed_sha and event.head_sha and reviewed_sha != event.head_sha
        else f"Review the whole change against `{event.base_branch or 'the base branch'}`."
    )
    return (
        f"Review pull request {event.repository}#{event.number}: {event.title}\n\n"
        f"{event.body[:MAX_EVENT_BODY_CHARS]}\n\n{scope} Do not change or push code: say what "
        "must change (correctness, security, missing tests) on the lines it is about."
    )


def deliver_instructions(schema: dict[str, Any]) -> str:
    """How the run hands back its result. The schema is the mission's (bounded when saved) and
    sits in the run's opening message once, so every later step reads it from the cache."""
    return (
        "When you have the result, call the `deliver` tool once with `output` following this "
        "JSON Schema; the platform publishes it after the run (you do not post it yourself):\n"
        f"```json\n{json.dumps(schema, separators=(',', ':'))}\n```"
    )


def prompt_for(
    mission: Mission, event: TriggerEvent, *, follow_up: bool, reviewed_sha: str = ""
) -> str:
    """What the agent is asked: the mission's instructions, the event in its own words, and how
    to hand back the result."""
    task = _task(mission, event, follow_up, reviewed_sha)
    parts = [mission.instructions.strip(), task]
    if event.url:
        parts.append(f"Source: {event.url}")
    schema = delivery_schema(mission.deliveries)
    if schema is not None:
        parts.append(deliver_instructions(schema))
    prompt = "\n\n".join(part for part in parts if part)
    repository = event.repository or mission.git_repo
    if repository and mission.mode != "review":
        prompt = with_repository_hint(
            prompt, repository, event.branch or mission.git_branch or None
        )
    elif repository:
        prompt = with_repository_hint(prompt, repository, event.branch or None)
    return prompt


def _new_session(
    session_id: str, mission: Mission, event: TriggerEvent, owner: str, run_id: str
) -> SessionRecord:
    metadata: dict[str, Any] = {
        MISSION_ID_KEY: mission.id,
        RUN_ID_KEY: run_id,
        "trigger_source": event.source,
        AUTO_FIX_KEY: mission.auto_fix,
        "sender": event.actor,
        "title_source": "auto",
        # Shared missions' sessions are their scope's to see; a personal one is its owner's.
        "is_shared": mission.scope == "org",
        "shared_teams": [mission.team_id] if mission.team_id else [],
    }
    schema = delivery_schema(mission.deliveries)
    if schema is not None:
        metadata.update({SCHEMA_KEY: schema, "toolsets": [MISSION_TOOLSET]})
    if event.repository:
        metadata["github_repo" if event.source.startswith("github") else "git_repo"] = (
            event.repository
        )
    if event.number:
        metadata["github_issue"] = event.number
    if event.key:
        metadata["ticket_key"] = event.key
    agent_id = mission.agent_id or (REVIEWER_AGENT_ID if mission.mode == "review" else "")
    for key, value in (
        ("agent_id", agent_id),
        ("permission_mode", mission.permission_mode),
        ("sandbox_id", mission.sandbox_type),
    ):
        if value:
            metadata[key] = value
    if mission.mode == "review" and event.head_sha:
        metadata[REVIEWED_SHA_KEY] = event.head_sha
    where = f"{event.repository}#{event.number}" if event.number else (event.key or "")
    title = (
        f"{mission.name} · {where}"
        if where
        else f"{mission.name} · {time.strftime('%Y-%m-%d %H:%M', time.gmtime())} UTC"
    )
    return SessionRecord(
        session_id=session_id,
        tenant_org_id=mission.org_id,
        tenant_user_id=owner,
        is_shared=mission.scope == "org",
        title=title[:120],
        # An event from a forge or tracker keeps the source its sessions always had; a run the
        # mission starts itself (schedule, its webhook, Run now) is a scheduled session.
        source=SessionSource.SCHEDULED
        if event.source in OWN_SOURCES
        else SessionSource.GITHUB_WEBHOOK,
        sandbox_status=SandboxStatus.NON_EXISTENT,
        metadata=metadata,
    )


def _for_this_run(mission: Mission, event: TriggerEvent, run_id: str) -> Any:
    """Point an existing session at this run (its schema and its run id)."""
    schema = delivery_schema(mission.deliveries)

    def apply(record: SessionRecord) -> None:
        known = dict(record.metadata) if isinstance(record.metadata, dict) else {}
        known.pop(OUTPUT_KEY, None)
        known[RUN_ID_KEY] = run_id
        if schema is not None:
            known[SCHEMA_KEY] = schema
            known["toolsets"] = sorted({*known.get("toolsets", []), MISSION_TOOLSET})
        if mission.mode == "review" and event.head_sha:
            known[REVIEWED_SHA_KEY] = event.head_sha
        record.metadata = known

    return apply


async def sync_active_repository(
    driver: SandboxDriverProtocol | None, session: SessionRecord
) -> None:
    """Bring a running session's active repository up to date before a follow-up, best effort.
    A stopped sandbox is left alone: it starts when the turn needs it, and the agent fetches."""
    if driver is None or await driver.get_status(session.session_id) != SandboxStatus.RUNNING:
        return
    try:
        folder = await resolve_repository_dir(
            driver, session.session_id, active=active_repository_of(session)
        )
    except RepositoryNotFoundError:
        return
    await driver.exec_command(
        session.session_id,
        "git fetch origin && git pull origin HEAD --rebase || true",
        workdir=folder,
        timeout_seconds=SYNC_TIMEOUT_SECONDS,
    )


# -- starting and ending ---------------------------------------------------------------------


async def start_run(
    app: FastAPI, mission: Mission, event: TriggerEvent, fallback_owner: str = ""
) -> RunOutcome:
    """Create the run's session (or wake the item's), record the run and start its turn."""
    state = app.state
    owner = await run_identity(app, mission, fallback_owner or mission.created_by)
    session_id = session_id_for(mission, event)
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    token = current_tenant_org_id.set(mission.org_id)
    try:
        db_mgr = getattr(state, "db_manager", None)
        if db_mgr is not None and getattr(db_mgr, "session_factory", None) is not None:
            async with db_mgr.session(tenant_org_id=mission.org_id) as session:
                await ensure_tenant_identity(session, mission.org_id, owner)
        existing = await state.session_store.get_session(session_id, include_history=False)
        metadata = (
            existing.metadata
            if existing is not None and isinstance(existing.metadata, dict)
            else {}
        )
        reviewed = str(metadata.get(REVIEWED_SHA_KEY) or "")
        if mission.mode == "review" and event.head_sha and reviewed == event.head_sha:
            return RunOutcome("ignored", session_id, mission.id, reason="This commit was reviewed.")
        if existing is None:
            await state.session_store.create_session(
                _new_session(session_id, mission, event, owner, run_id)
            )
        else:
            await sync_active_repository(getattr(state, "sandbox_driver", None), existing)
            await state.session_store.mutate_session(
                session_id, mission.org_id, _for_this_run(mission, event, run_id)
            )
        await _record_and_spawn(app, mission, event, owner, session_id, run_id, existing, reviewed)
    finally:
        current_tenant_org_id.reset(token)
    await mission_store(app).mark_run(mission.org_id, mission.id, session_id, "running")
    return RunOutcome("resumed" if existing else "created", session_id, mission.id, run_id)


def _item(event: TriggerEvent) -> dict[str, Any]:
    return {
        "source": event.source,
        "repository": event.repository,
        "number": event.number,
        "key": event.key,
        "head_sha": event.head_sha,
        "url": event.url,
        "actor": event.actor,
    }


async def _record_and_spawn(
    app: FastAPI,
    mission: Mission,
    event: TriggerEvent,
    owner: str,
    session_id: str,
    run_id: str,
    existing: SessionRecord | None,
    reviewed_sha: str,
) -> None:
    state = app.state
    await RunStore(getattr(state, "db_manager", None)).create(
        MissionRun(
            id=run_id,
            org_id=mission.org_id,
            mission_id=mission.id,
            session_id=session_id,
            source=event.source,
            title=event.title or mission.name,
            started_at=int(time.time()),
        ),
        _item(event),
    )
    prompt = prompt_for(
        mission,
        event,
        follow_up=existing is not None
        and mission.mode != "review"
        and event.source not in REVIEW_SOURCES,
        reviewed_sha=reviewed_sha,
    )
    task = _spawn_background_turn(
        session_id=session_id,
        prompt=prompt,
        orchestrator=state.orchestrator,
        event_bus=state.event_bus,
        driver=getattr(state, "sandbox_driver", None),
        registry=getattr(state, "tool_registry", None),
        store=state.session_store,
        model=f"tier:{mission.model_tier}" if mission.model_tier else None,
        git_engine=getattr(state, "git_engine", None),
        db_mgr=getattr(state, "db_manager", None),
        author_user_id=owner,
        attachment_service=getattr(state, "attachment_service", None),
        concurrency_gate=getattr(state, "global_concurrency_gate", None),
        session_locks=getattr(state, "session_write_locks", None),
    )
    task.add_done_callback(lambda _done: complete_run_soon(app, mission.org_id, run_id))
