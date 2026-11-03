"""A mission's result: the ``deliver`` tool that takes it, and the platform that publishes it.

The agent never posts a mission's result itself. It calls ``deliver`` with an ``output`` that
must follow the run's schema (``delivery_schema``: one part per delivery), checked here with
JSON Schema; a refusal says why and the agent calls again. The output is kept in the session's
metadata. When the turn ends, ``complete_run`` queues one ``mission_deliveries`` row per
delivery before any attempt (a text delivery falls back to the run's answer; a structured one
without a result fails), then each row is posted (Slack, a signed webhook, a GitHub review or
comment, with the organization's App) and retried with backoff by any replica until it lands or
runs out of attempts. So the agent of a review mission can run read-only, and the platform still
posts the review.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import jsonschema
from agent_core.context import current_execution_ctx
from agent_core.tools import github_prs
from agent_core.tools.safe_http import safe_post
from config_engine.crypto import CredentialCipher
from fastapi import FastAPI
from git_engine.engine import pick_installation
from sqlalchemy import select, update

from api.db.models import MissionDeliveryModel
from api.db.session import current_tenant_org_id
from api.services.git_credentials import app_may_act_on
from api.services.git_gateway_env import github_api_url_of
from api.services.github_installations import installations_for_org
from api.services.mission_records import (
    MISSION_TOOLSET,
    OUTPUT_KEY,
    RUN_ID_KEY,
    SCHEMA_KEY,
    RunStore,
)
from api.services.missions import MissionStore
from api.services.outbound_webhooks import next_attempt_delay, sign
from api.services.settings import effective_settings
from api.turns.failure import FAILED_ANSWER_PREFIX
from specifications.interfaces.agent import GithubAccess
from specifications.interfaces.automation import (
    TEXT_DELIVERIES,
    Delivery,
    Mission,
    MissionRun,
)
from specifications.interfaces.llm import ToolDefinition
from specifications.interfaces.session import SessionRecord

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 60_000
MAX_ATTEMPTS = 6
SEND_TIMEOUT_SECONDS = 15
_cipher = CredentialCipher()
_memory: dict[str, dict[str, Any]] = {}
_background: set[asyncio.Task[None]] = set()

DELIVER_TOOL = ToolDefinition(
    name="deliver",
    description=(
        "Hand this mission's result to the platform, which publishes it after the run (to the "
        "chat, webhook, pull request review or comment the mission names). Call it once you "
        "have the result, with `output` following the JSON Schema in the mission's request; a "
        "result that does not fit is refused with the reason, so fix it and call again."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "output": {"type": "object", "description": "The result, per the mission's schema."}
        },
        "required": ["output"],
    },
)


def reset_memory() -> None:
    _memory.clear()


# -- the deliver tool ------------------------------------------------------------------------


def register_deliver_tool(app: FastAPI, registry: Any) -> None:
    """Offer ``deliver`` to the turns of mission runs with something to deliver (its toolset)."""

    async def deliver(output: dict[str, Any]) -> str:
        ctx = current_execution_ctx.get()
        if ctx is None or not ctx.session_id:
            return "Error: `deliver` belongs to a mission run, and this call has none."
        return await accept_output(app, ctx.tenant_org_id, ctx.session_id, output)

    registry.register_dynamic_tool(DELIVER_TOOL, deliver, toolset=MISSION_TOOLSET)


async def accept_output(app: FastAPI, org_id: str, session_id: str, output: Any) -> str:
    """Check ``output`` against the run's schema and keep it for the deliveries."""
    store = app.state.session_store
    record: SessionRecord | None = await store.get_session(session_id, include_history=False)
    metadata = record.metadata if record is not None and isinstance(record.metadata, dict) else {}
    schema = metadata.get(SCHEMA_KEY)
    if not isinstance(schema, dict):
        return "Error: this session has nothing to deliver; answer normally instead."
    if len(json.dumps(output, default=str)) > MAX_OUTPUT_CHARS:
        return f"Rejected: the result is over {MAX_OUTPUT_CHARS:,} characters. Shorten it and call again."
    problem = schema_problem(output, schema)
    if problem:
        return f"Rejected: {problem}. Call `deliver` again with a corrected `output`."
    run_id = str(metadata.get(RUN_ID_KEY) or "")

    def keep(target: SessionRecord) -> None:
        known = dict(target.metadata) if isinstance(target.metadata, dict) else {}
        known[OUTPUT_KEY] = {"run_id": run_id, "output": output}
        target.metadata = known

    await store.mutate_session(session_id, org_id, keep)
    return "Result received: the platform publishes it when this run ends. Finish with a short summary."


def schema_problem(output: Any, schema: dict[str, Any]) -> str | None:
    """The first way ``output`` breaks ``schema`` (where and why), or ``None``."""
    validator = jsonschema.Draft202012Validator(schema)
    error = next(iter(sorted(validator.iter_errors(output), key=lambda e: list(e.path))), None)
    if error is None:
        return None
    where = "/".join(str(part) for part in error.absolute_path) or "output"
    return f"`{where}`: {error.message}"[:600]


# -- the end of a run ------------------------------------------------------------------------


def complete_run_soon(app: FastAPI, org_id: str, run_id: str) -> None:
    """Settle a run once its turn ended (called from the turn task's done callback)."""
    task = asyncio.create_task(_complete_quietly(app, org_id, run_id))
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _complete_quietly(app: FastAPI, org_id: str, run_id: str) -> None:
    await complete_run(app, org_id, run_id)


def _answer_of(record: SessionRecord | None) -> str:
    answers = [m for m in (record.conversation_history if record else []) if m.role == "assistant"]
    return str(answers[-1].content or "") if answers else ""


async def complete_run(app: FastAPI, org_id: str, run_id: str) -> str:
    """Queue the run's deliveries and send them; the run's status afterwards."""
    token = current_tenant_org_id.set(org_id)
    try:
        runs = RunStore(getattr(app.state, "db_manager", None))
        found = await runs.get(org_id, run_id)
        if found is None:
            return "gone"
        run, item = found
        missions = mission_store(app)
        mission = await missions.get(org_id, run.mission_id)
        record = await app.state.session_store.get_session(run.session_id)
        answer = _answer_of(record)
        if mission is None or not answer or answer.startswith(FAILED_ANSWER_PREFIX):
            await runs.finish(org_id, run_id, "failed", [])
            if mission is not None:
                await missions.mark_run(org_id, mission.id, run.session_id, "failed")
            return "failed"
        metadata = record.metadata if record and isinstance(record.metadata, dict) else {}
        kept = metadata.get(OUTPUT_KEY)
        output = (
            kept.get("output") if isinstance(kept, dict) and kept.get("run_id") == run_id else None
        )
        queued = await _queue(app, mission, run, item, output, answer)
        return await _send_and_settle(app, mission, run, queued)
    except Exception:
        logger.exception("Mission run %s could not be completed", run_id)
        return "failed"
    finally:
        current_tenant_org_id.reset(token)


def _payload_for(delivery: Delivery, output: Any, answer: str) -> dict[str, Any] | None:
    """What one delivery publishes: its part of the run's output, else (text deliveries only)
    the run's answer; ``None`` when there is nothing to publish."""
    part = output.get(delivery.kind) if isinstance(output, dict) else None
    if isinstance(part, dict):
        return part
    if delivery.kind == "chat":
        return {"text": answer}
    if delivery.kind == "github_comment":
        return {"body": answer}
    return None


async def _queue(
    app: FastAPI,
    mission: Mission,
    run: MissionRun,
    item: dict[str, Any],
    output: Any,
    answer: str,
) -> list[tuple[str, Delivery, str]]:
    """One stored row per delivery, before any attempt: (row id, delivery, error so far)."""
    queued: list[tuple[str, Delivery, str]] = []
    now = int(time.time())
    for delivery in mission.deliveries:
        if delivery.kind == "session":
            continue
        payload = _payload_for(delivery, output, answer)
        if payload is None:
            reason = "the run did not call `deliver` with a result for it"
            queued.append(("", delivery, reason))
            continue
        row_id = f"mdl_{uuid.uuid4().hex[:16]}"
        stored = {
            "output": payload,
            "target": {"bot_id": delivery.bot_id, "channel": delivery.channel, "url": delivery.url},
            "mission": {"id": mission.id, "name": mission.name},
            "run": {"id": run.id, "session_id": run.session_id},
            "item": item,
            "answer_used": delivery.kind in TEXT_DELIVERIES and not isinstance(output, dict),
        }
        await _insert(app, mission.org_id, row_id, run.id, delivery.kind, stored, now)
        queued.append((row_id, delivery, ""))
    return queued


async def _insert(
    app: FastAPI,
    org_id: str,
    row_id: str,
    run_id: str,
    kind: str,
    payload: dict[str, Any],
    now: int,
) -> None:
    db_mgr = _db(app)
    row = {
        "id": row_id, "org_id": org_id, "run_id": run_id, "kind": kind, "payload": payload,
        "status": "pending", "attempts": 0, "next_attempt_at": now, "last_error": "",
        "created_at": now,
    }  # fmt: skip
    if db_mgr is None:
        _memory[row_id] = row
        return
    async with db_mgr.session(tenant_org_id=org_id) as session:
        session.add(MissionDeliveryModel(**row))


async def _send_and_settle(
    app: FastAPI, mission: Mission, run: MissionRun, queued: list[tuple[str, Delivery, str]]
) -> str:
    results: list[dict[str, str]] = []
    for row_id, delivery, error in queued:
        status = "failed" if error else await attempt(app, mission.org_id, row_id)
        results.append(
            {
                "kind": delivery.kind,
                "status": status,
                "error": error or await _error_of(app, mission.org_id, row_id),
            }
        )
    return await _settle(app, mission, run, results)


async def _settle(
    app: FastAPI, mission: Mission, run: MissionRun, results: list[dict[str, str]]
) -> str:
    if not results:
        status = "completed"
    elif all(r["status"] == "delivered" for r in results):
        status = "delivered"
    elif any(r["status"] == "pending" for r in results):
        status = "completed"
    else:
        status = "delivery_failed"
    await RunStore(_db(app)).finish(mission.org_id, run.id, status, results)
    await mission_store(app).mark_run(mission.org_id, mission.id, run.session_id, status)
    return status


# -- attempts --------------------------------------------------------------------------------


async def _load(app: FastAPI, org_id: str, row_id: str) -> dict[str, Any] | None:
    db_mgr = _db(app)
    if db_mgr is None:
        row = _memory.get(row_id)
        return dict(row) if row and row["org_id"] == org_id else None
    async with db_mgr.session(tenant_org_id=org_id) as session:
        found = await session.get(MissionDeliveryModel, row_id)
        if found is None or found.org_id != org_id:
            return None
        return {c.name: getattr(found, c.name) for c in MissionDeliveryModel.__table__.columns}


async def _claim(app: FastAPI, row: dict[str, Any]) -> bool:
    """Move the row's due time past this attempt; a replica that loses the race skips it."""
    lease = int(time.time()) + SEND_TIMEOUT_SECONDS * 4
    db_mgr = _db(app)
    if db_mgr is None:
        held = _memory.get(row["id"])
        if held is None or held["next_attempt_at"] != row["next_attempt_at"]:
            return False
        held["next_attempt_at"] = lease
        return True
    async with db_mgr.session(tenant_org_id=row["org_id"]) as session:
        result = await session.execute(
            update(MissionDeliveryModel)
            .where(
                MissionDeliveryModel.id == row["id"],
                MissionDeliveryModel.next_attempt_at == row["next_attempt_at"],
            )
            .values(next_attempt_at=lease)
        )
        return bool(getattr(result, "rowcount", 0))


async def _record(app: FastAPI, row: dict[str, Any], error: str) -> str:
    attempts = int(row["attempts"]) + 1
    status = "delivered" if not error else ("failed" if attempts >= MAX_ATTEMPTS else "pending")
    values = {
        "status": status,
        "attempts": attempts,
        "last_error": error[:1000],
        "next_attempt_at": int(time.time()) + next_attempt_delay(attempts),
    }
    db_mgr = _db(app)
    if db_mgr is None:
        _memory[row["id"]].update(values)
        return status
    async with db_mgr.session(tenant_org_id=row["org_id"]) as session:
        await session.execute(
            update(MissionDeliveryModel)
            .where(MissionDeliveryModel.id == row["id"])
            .values(**values)
        )
    return status


async def _error_of(app: FastAPI, org_id: str, row_id: str) -> str:
    row = await _load(app, org_id, row_id) if row_id else None
    return str(row["last_error"]) if row else ""


async def attempt(app: FastAPI, org_id: str, row_id: str) -> str:
    """Post one queued result; its status afterwards (delivered, pending or failed)."""
    row = await _load(app, org_id, row_id)
    if row is None or row["status"] != "pending":
        return str(row["status"]) if row else "gone"
    if not await _claim(app, row):
        return "pending"
    try:
        await _SENDERS[row["kind"]](app, org_id, row["payload"])
        error = ""
    except DeliveryError as err:
        error = str(err)
    except Exception as err:
        error = f"{type(err).__name__}: {err}"[:500]
    return await _record(app, row, error)


async def retry_due(app: FastAPI, admin_db: Any, limit: int = 100) -> int:
    """Attempt every organization's due deliveries again (schema-owner read, tenant writes)."""
    now = int(time.time())
    if admin_db is None:
        due = [
            (r["org_id"], r["id"])
            for r in _memory.values()
            if r["status"] == "pending" and r["next_attempt_at"] <= now
        ][:limit]
    else:
        async with admin_db.session() as session:
            rows = await session.execute(
                select(MissionDeliveryModel.org_id, MissionDeliveryModel.id)
                .where(
                    MissionDeliveryModel.status == "pending",
                    MissionDeliveryModel.next_attempt_at <= now,
                )
                .limit(limit)
            )
            due = [(org_id, row_id) for org_id, row_id in rows]
    for org_id, row_id in due:
        token = current_tenant_org_id.set(org_id)
        try:
            await attempt(app, org_id, row_id)
        finally:
            current_tenant_org_id.reset(token)
    return len(due)


# -- senders ---------------------------------------------------------------------------------


class DeliveryError(RuntimeError):
    """A delivery that could not be posted, with a reason a person can act on."""


async def _send_chat(app: FastAPI, org_id: str, payload: dict[str, Any]) -> None:
    manager = getattr(app.state, "slack_bot_manager", None)
    bots = manager.for_org(org_id) if manager is not None else []
    wanted = str(payload["target"].get("bot_id") or "")
    bot = next((b for b in bots if not wanted or getattr(b.bot, "id", "") == wanted), None)
    if bot is None:
        raise DeliveryError("No running Slack bot of the organization can post it.")
    posted = await bot.post_mission_report(
        org_id=org_id,
        channel_id=str(payload["target"].get("channel") or ""),
        mission_name=str(payload["mission"]["name"]),
        session_id=str(payload["run"]["session_id"]),
        answer=str(payload["output"].get("text") or ""),
        succeeded=True,
    )
    if not posted:
        raise DeliveryError("Slack refused the message (channel not allowed, or bot not in it).")


async def _send_webhook(app: FastAPI, org_id: str, payload: dict[str, Any]) -> None:
    mission_id = str(payload["mission"]["id"])
    sealed = (await mission_store(app).secrets(org_id, mission_id)).get("webhook")
    if not sealed:
        raise DeliveryError("The webhook's signing secret is missing; save the mission again.")
    body = json.dumps(
        {
            "mission": payload["mission"],
            "run": payload["run"],
            "item": payload["item"],
            "output": payload["output"],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    timestamp = int(time.time())
    status, _ = await safe_post(
        str(payload["target"].get("url") or ""),
        content=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "RocketChat-Missions/1",
            "X-Rocket-Event": "mission.result",
            "X-Rocket-Timestamp": str(timestamp),
            "X-Rocket-Signature": sign(_cipher.decrypt(str(sealed)), timestamp, body),
        },
        timeout_seconds=SEND_TIMEOUT_SECONDS,
    )
    if not 200 <= status < 300:
        raise DeliveryError(f"The webhook answered HTTP {status}.")


async def _github_access(app: FastAPI, org_id: str, repository: str) -> GithubAccess:
    """The organization's App installation for ``repository`` (the platform publishes, never
    the person who triggered the run)."""
    engine = getattr(app.state, "git_engine", None)
    db_mgr = getattr(app.state, "db_manager", None)
    if engine is None or not app_may_act_on(
        await effective_settings(db_mgr, org_id, "github"), repository
    ):
        raise DeliveryError(f"The organization's GitHub App may not act on {repository}.")
    mapped = await installations_for_org(db_mgr, org_id)
    token = await engine.get_github_access_token(
        repo=repository,
        repositories=[repository.rsplit("/", 1)[-1]],
        installation_id=pick_installation(
            [(i.installation_id, i.account_login) for i in mapped], repository
        ),
        inherit_permissions=True,
    )
    if not token:
        raise DeliveryError(f"No GitHub App installation of the organization covers {repository}.")
    return GithubAccess(token, github_api_url_of(engine), "the organization's GitHub App")


def _item_target(payload: dict[str, Any]) -> tuple[str, int]:
    item = payload.get("item") or {}
    repository, number = str(item.get("repository") or ""), int(item.get("number") or 0)
    if not repository or not number:
        raise DeliveryError("The run is not about a pull request or issue to post on.")
    return repository, number


async def _send_github_review(app: FastAPI, org_id: str, payload: dict[str, Any]) -> None:
    repository, number = _item_target(payload)
    output = payload["output"]
    await github_prs.submit_review(
        await _github_access(app, org_id, repository),
        repository,
        number,
        event=str(output.get("event") or "comment"),
        body=str(output.get("body") or ""),
        comments=list(output.get("comments") or []),
    )


async def _send_github_comment(app: FastAPI, org_id: str, payload: dict[str, Any]) -> None:
    repository, number = _item_target(payload)
    await github_prs.post_comment(
        await _github_access(app, org_id, repository),
        repository,
        number,
        str(payload["output"].get("body") or ""),
    )


_SENDERS = {
    "chat": _send_chat,
    "webhook": _send_webhook,
    "github_review": _send_github_review,
    "github_comment": _send_github_comment,
}


def _db(app: FastAPI) -> Any:
    db_mgr = getattr(app.state, "db_manager", None)
    return db_mgr if db_mgr is not None and getattr(db_mgr, "session_factory", None) else None


def mission_store(app: FastAPI) -> MissionStore:
    """The app's mission store, made on first use and again whenever the app's database changed
    (a store bound to a closed manager would fail every read)."""
    store = getattr(app.state, "mission_store", None)
    db_mgr = getattr(app.state, "db_manager", None)
    if not isinstance(store, MissionStore) or store.bound_to is not db_mgr:
        store = MissionStore(db_mgr)
        app.state.mission_store = store
    return store
