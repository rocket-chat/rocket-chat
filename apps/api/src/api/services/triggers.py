"""An organization's triggers, and starting (or waking) the session an event asks for.

Every source ends here: the GitHub and GitLab webhooks, Jira and Linear, a schedule and the
API parse their event into a ``TriggerEvent``; the organization's triggers decide
(``specifications.interfaces.automation.matches``), and ``start_from_trigger`` creates a blank
session (the agent clones what it needs) or wakes the one the event is about.

Deliveries are claimed in ``webhook_deliveries`` under a unique key before anything starts, so
several replicas receiving the same delivery start one session.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, replace
from typing import Any

from fastapi import FastAPI
from sandbox_driver.workspace import RepositoryNotFoundError, resolve_repository_dir
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from api.db.models import TriggerModel, WebhookDeliveryModel
from api.db.session import current_tenant_org_id
from api.services.github_watches import AUTO_FIX_KEY
from api.services.repository_hint import with_repository_hint
from api.services.session_support import active_repository_of
from api.turns.tasks import _spawn_background_turn
from specifications.interfaces.automation import (
    MODEL_TIERS,
    REVIEW_SOURCES,
    TRIGGER_MODES,
    TRIGGER_SOURCES,
    Trigger,
    TriggerEvent,
    TriggerFilter,
    matches,
)
from specifications.interfaces.sandbox import SandboxDriverProtocol, SandboxStatus
from specifications.interfaces.session import SessionRecord, SessionSource

# Deliveries are remembered this long: the forges retry a delivery for minutes, not days.
DELIVERY_RETENTION_SECONDS = 24 * 3600
# The event text an agent reads is the event's own (an issue body, a comment): bounded.
MAX_EVENT_BODY_CHARS = 8000
TRIGGER_ID_KEY = "trigger_id"
# The built-in reviewer answers a review trigger that names no agent.
REVIEWER_AGENT_ID = "persona-reviewer"
REVIEWED_SHA_KEY = "review_last_sha"
SYNC_TIMEOUT_SECONDS = 120

# Without a database (development and tests): by organization, then trigger id; and the claimed
# deliveries.
_memory: dict[str, dict[str, Trigger]] = {}
_claimed: dict[tuple[str, str, str], float] = {}


class TriggerError(ValueError):
    """A trigger that cannot be saved as given."""


@dataclass(frozen=True)
class TriggerOutcome:
    status: str  # created, resumed, ignored, duplicate
    session_id: str = ""
    trigger_id: str = ""
    reason: str = ""


# -- storage -----------------------------------------------------------------------------------


def _from_row(row: TriggerModel) -> Trigger:
    raw = row.filter if isinstance(row.filter, dict) else {}
    return Trigger(
        id=row.id,
        org_id=row.org_id,
        name=row.name,
        source=row.source,
        filter=TriggerFilter(
            **{
                key: tuple(str(v) for v in raw.get(key) or ())
                for key in TriggerFilter.__dataclass_fields__
            }
        ),
        agent_id=row.agent_id,
        permission_mode=row.permission_mode,
        model_tier=row.model_tier,
        sandbox_type=row.sandbox_type,
        mode=row.mode,
        auto_fix=row.auto_fix,
        instructions=row.instructions,
        schedule=row.schedule,
        enabled=row.enabled,
    )


def validate(trigger: Trigger) -> Trigger:
    """The trigger as stored, or ``TriggerError`` naming what is wrong."""
    if trigger.source not in TRIGGER_SOURCES:
        raise TriggerError(f"Unknown source '{trigger.source}'.")
    if trigger.mode not in TRIGGER_MODES:
        raise TriggerError(f"Unknown mode '{trigger.mode}'.")
    if trigger.mode == "review" and trigger.source not in REVIEW_SOURCES:
        raise TriggerError("Review mode is for pull request (merge request) sources only.")
    if trigger.model_tier not in MODEL_TIERS:
        raise TriggerError(f"Unknown model tier '{trigger.model_tier}'.")
    if trigger.source == "schedule" and not _CRON.match(trigger.schedule.strip()):
        raise TriggerError("A schedule trigger needs a cron expression of five fields.")
    if not trigger.name.strip():
        raise TriggerError("A trigger needs a name.")
    return replace(
        trigger, name=trigger.name.strip()[:120], instructions=trigger.instructions[:4000]
    )


_CRON = re.compile(r"^\S+(\s+\S+){4}$")


async def list_triggers(db_mgr: Any, org_id: str) -> list[Trigger]:
    if db_mgr is None:
        return sorted(_memory.get(org_id, {}).values(), key=lambda t: t.name)
    async with db_mgr.session(tenant_org_id=org_id) as session:
        rows = (
            await session.execute(
                select(TriggerModel)
                .where(TriggerModel.org_id == org_id)
                .order_by(TriggerModel.name)
            )
        ).scalars()
        return [_from_row(row) for row in rows]


async def save_trigger(db_mgr: Any, trigger: Trigger, actor: str) -> Trigger:
    """Create (empty id) or replace a trigger of its organization."""
    trigger = validate(replace(trigger, id=trigger.id or f"trg_{uuid.uuid4().hex[:12]}"))
    if db_mgr is None:
        _memory.setdefault(trigger.org_id, {})[trigger.id] = trigger
        return trigger
    now = int(time.time())
    async with db_mgr.session(tenant_org_id=trigger.org_id) as session:
        row = await session.get(TriggerModel, trigger.id)
        if row is None or row.org_id != trigger.org_id:
            row = TriggerModel(
                id=trigger.id, org_id=trigger.org_id, created_by=actor, created_at=now
            )
            session.add(row)
        row.name, row.source, row.filter = trigger.name, trigger.source, _filter_dict(trigger)
        row.agent_id, row.permission_mode = trigger.agent_id, trigger.permission_mode
        row.model_tier, row.sandbox_type, row.mode = (
            trigger.model_tier,
            trigger.sandbox_type,
            trigger.mode,
        )
        row.auto_fix, row.instructions, row.schedule = (
            trigger.auto_fix,
            trigger.instructions,
            trigger.schedule,
        )
        row.enabled, row.updated_at = trigger.enabled, now
    return trigger


async def delete_trigger(db_mgr: Any, org_id: str, trigger_id: str) -> bool:
    if db_mgr is None:
        return _memory.get(org_id, {}).pop(trigger_id, None) is not None
    async with db_mgr.session(tenant_org_id=org_id) as session:
        result = await session.execute(
            delete(TriggerModel).where(TriggerModel.id == trigger_id, TriggerModel.org_id == org_id)
        )
        return bool(getattr(result, "rowcount", 0))


def _filter_dict(trigger: Trigger) -> dict[str, Any]:
    return {key: list(value) for key, value in asdict(trigger.filter).items()}


def default_triggers(org_id: str) -> list[Trigger]:
    """What an organization without triggers gets: the deployment's label (``GITHUB_TRIGGER_LABEL``)
    on an issue, and comments that address the agent or sit on such an issue."""
    label = os.getenv("GITHUB_TRIGGER_LABEL", "ai-fix")
    return [
        Trigger(
            id="default_issue_label",
            org_id=org_id,
            name=f"Issues labelled {label}",
            source="github_issue_label",
            filter=TriggerFilter(labels=(label,)),
        ),
        Trigger(
            id="default_issue_comment",
            org_id=org_id,
            name="Comments addressed to the agent",
            source="github_issue_comment",
        ),
        Trigger(
            id="default_issue_comment_label",
            org_id=org_id,
            name=f"Comments on issues labelled {label}",
            source="github_issue_comment",
            filter=TriggerFilter(labels=(label,)),
        ),
    ]


async def matching_trigger(db_mgr: Any, org_id: str, event: TriggerEvent) -> Trigger | None:
    """The first of the organization's triggers (or the defaults, when it has none) that takes
    the event."""
    configured = await list_triggers(db_mgr, org_id)
    candidates = configured or default_triggers(org_id)
    return next((t for t in candidates if matches(t, event)), None)


# -- de-duplication -----------------------------------------------------------------------------


async def claim_delivery(db_mgr: Any, org_id: str, source: str, delivery_id: str) -> bool:
    """Whether this replica is the first to handle the delivery (a delivery without an id is
    always handled: the source gave nothing to recognise it by)."""
    if not delivery_id:
        return True
    if db_mgr is None:
        key = (org_id, source, delivery_id)
        now = time.time()
        for stale in [k for k, at in _claimed.items() if now - at > DELIVERY_RETENTION_SECONDS]:
            _claimed.pop(stale, None)
        if key in _claimed:
            return False
        _claimed[key] = now
        return True
    try:
        async with db_mgr.session(tenant_org_id=org_id) as session:
            session.add(
                WebhookDeliveryModel(
                    id=f"dlv_{uuid.uuid4().hex[:16]}",
                    org_id=org_id,
                    source=source,
                    delivery_id=delivery_id[:128],
                )
            )
    except IntegrityError:
        return False
    return True


async def forget_old_deliveries(admin_db: Any) -> int:
    """Retention: deliveries older than a day are not redelivered any more. Every
    organization's, on the schema-owner connection."""
    if admin_db is None:
        return 0
    cutoff = int(time.time()) - DELIVERY_RETENTION_SECONDS
    async with admin_db.session() as session:
        result = await session.execute(
            delete(WebhookDeliveryModel).where(WebhookDeliveryModel.created_at < cutoff)
        )
        return int(getattr(result, "rowcount", 0) or 0)


# -- starting sessions --------------------------------------------------------------------------


def session_id_for(trigger: Trigger, event: TriggerEvent) -> str:
    """One session per item and trigger mode: a review and a fix of one pull request are two
    sessions; a failed run without a pull request is keyed by its commit."""
    item = (
        str(event.number)
        if event.number
        else event.key or (event.head_sha[:12] if event.head_sha else uuid.uuid4().hex[:10])
    )
    # A ticket key (``ENG-3``) names itself; a repository item needs its repository.
    scoped = f"{event.repository}_{item}" if event.repository else item
    slug = re.sub(r"[^a-z0-9_]+", "_", scoped.lower()).strip("_")
    prefix = {"review": "review"}.get(trigger.mode, _PREFIXES.get(event.source, "trg"))
    return f"{prefix}_{slug}"[:64]


_PREFIXES = {
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


def prompt_for(
    trigger: Trigger, event: TriggerEvent, *, follow_up: bool, reviewed_sha: str = ""
) -> str:
    """What the agent is asked: the trigger's instructions, then the event in its own words."""
    body = event.body[:MAX_EVENT_BODY_CHARS]
    where = (
        f"{event.repository}#{event.number}" if event.number else (event.key or event.repository)
    )
    if trigger.mode == "review":
        task = review_prompt(event, reviewed_sha)
    elif follow_up:
        task = f"Follow-up from @{event.actor} on {where}:\n\n{body}"
    elif event.source in ("github_workflow_failed", "gitlab_pipeline_failed"):
        task = (
            f"CI failed: {event.title}. Find the cause in the run's logs ({event.url}), fix it on "
            f"branch `{event.branch}` and push the fix.\n\n{body}"
        ).strip()
    else:
        branch = event.branch or f"agent/{(event.key or str(event.number)).lower()}-fix"
        task = (
            f"Resolve {where}: {event.title}\n\n{body}\n\nDo the work on a branch named "
            f"`{branch}` and open a pull request that references it."
        )
    if trigger.instructions:
        task = f"{trigger.instructions.strip()}\n\n{task}"
    if event.url:
        task = f"{task}\n\nSource: {event.url}"
    if event.repository and trigger.mode != "review":
        task = with_repository_hint(task, event.repository, event.branch or None)
    return task


def review_prompt(event: TriggerEvent, reviewed_sha: str) -> str:
    """Review the pull request (only the commits since ``reviewed_sha`` when it was reviewed)."""
    scope = (
        f"Review only the changes since the last review: `git diff {reviewed_sha}..{event.head_sha}`."
        if reviewed_sha and event.head_sha and reviewed_sha != event.head_sha
        else f"Review the whole change against `{event.base_branch or 'the base branch'}`."
    )
    return with_repository_hint(
        f"Review pull request {event.repository}#{event.number}: {event.title}\n\n"
        f"{event.body[:MAX_EVENT_BODY_CHARS]}\n\n{scope} Do not change or push code. Post your "
        "result as one review: `github_pull_request` with actions ['review'], a short summary "
        "as `body`, an inline comment in `review_comments` on each changed line that needs a "
        "change (correctness, security, missing tests), and `event` request_changes when one "
        "must be fixed before merging, else comment.",
        event.repository,
        event.branch or None,
    )


async def start_from_trigger(
    app: FastAPI, trigger: Trigger, event: TriggerEvent, owner_user_id: str
) -> TriggerOutcome:
    """Create the event's session, or wake the existing one, and run the trigger's turn."""
    state = app.state
    session_id = session_id_for(trigger, event)
    token = current_tenant_org_id.set(trigger.org_id)
    try:
        existing = await state.session_store.get_session(session_id, include_history=False)
        metadata = (
            existing.metadata
            if existing is not None and isinstance(existing.metadata, dict)
            else {}
        )
        if (
            trigger.mode == "review"
            and event.head_sha
            and metadata.get(REVIEWED_SHA_KEY) == event.head_sha
        ):
            return TriggerOutcome("ignored", session_id, trigger.id, "This commit was reviewed.")
        if existing is None:
            await state.session_store.create_session(
                _new_session(session_id, trigger, event, owner_user_id)
            )
        else:
            await sync_active_repository(getattr(state, "sandbox_driver", None), existing)
            if trigger.mode == "review" and event.head_sha:
                await state.session_store.mutate_session(
                    session_id, trigger.org_id, _reviewed(event.head_sha)
                )
        prompt = prompt_for(
            trigger,
            event,
            follow_up=existing is not None and trigger.mode != "review",
            reviewed_sha=str(metadata.get(REVIEWED_SHA_KEY) or ""),
        )
        _spawn_background_turn(
            session_id=session_id,
            prompt=prompt,
            orchestrator=state.orchestrator,
            event_bus=state.event_bus,
            driver=getattr(state, "sandbox_driver", None),
            registry=getattr(state, "tool_registry", None),
            store=state.session_store,
            model=f"tier:{trigger.model_tier}" if trigger.model_tier else None,
            git_engine=getattr(state, "git_engine", None),
            db_mgr=getattr(state, "db_manager", None),
            author_user_id=owner_user_id,
            attachment_service=getattr(state, "attachment_service", None),
            concurrency_gate=getattr(state, "global_concurrency_gate", None),
            session_locks=getattr(state, "session_write_locks", None),
        )
    finally:
        current_tenant_org_id.reset(token)
    return TriggerOutcome("resumed" if existing else "created", session_id, trigger.id)


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


def _new_session(
    session_id: str, trigger: Trigger, event: TriggerEvent, owner_user_id: str
) -> SessionRecord:
    metadata: dict[str, Any] = {
        TRIGGER_ID_KEY: trigger.id,
        "trigger_source": event.source,
        AUTO_FIX_KEY: trigger.auto_fix,
        "sender": event.actor,
        "title_source": "auto",
    }
    if event.repository:
        metadata["github_repo" if event.source.startswith("github") else "git_repo"] = (
            event.repository
        )
    if event.number:
        metadata["github_issue"] = event.number
    if event.key:
        metadata["ticket_key"] = event.key
    agent_id = trigger.agent_id or (REVIEWER_AGENT_ID if trigger.mode == "review" else "")
    if agent_id:
        metadata["agent_id"] = agent_id
    if trigger.permission_mode:
        metadata["permission_mode"] = trigger.permission_mode
    if trigger.sandbox_type:
        metadata["sandbox_id"] = trigger.sandbox_type
    if trigger.mode == "review" and event.head_sha:
        metadata[REVIEWED_SHA_KEY] = event.head_sha
    verb = "Review" if trigger.mode == "review" else "Fix"
    where = (
        f"{event.repository}#{event.number}" if event.number else (event.key or event.repository)
    )
    return SessionRecord(
        session_id=session_id,
        tenant_org_id=trigger.org_id,
        tenant_user_id=owner_user_id,
        is_shared=True,
        title=f"{verb} {where}"[:120],
        source=_SOURCES.get(event.source, SessionSource.GITHUB_WEBHOOK),
        sandbox_status=SandboxStatus.NON_EXISTENT,
        metadata=metadata,
    )


_SOURCES = {
    "schedule": SessionSource.SCHEDULED,
}


def _reviewed(head_sha: str) -> Any:
    def remember(record: SessionRecord) -> None:
        known = record.metadata if isinstance(record.metadata, dict) else {}
        record.metadata = {**known, REVIEWED_SHA_KEY: head_sha}

    return remember
