"""Missions: schedule rules, the store, the API (ownership, run now, its webhook, presets) and how
a run's result is delivered."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from api.main import app
from api.services import mission_delivery
from api.services.mission_delivery import accept_output, complete_run, schema_problem
from api.services.mission_records import OUTPUT_KEY, SCHEMA_KEY, RunStore
from api.services.missions import (
    MAX_MISSIONS_PER_USER,
    MissionError,
    MissionStore,
    next_fire_after,
)
from api.services.outbound_webhooks import sign
from api.services.settings import reset_memory_settings, save_org_config
from fastapi.testclient import TestClient

from specifications.interfaces.automation import (
    Delivery,
    Mission,
    MissionRun,
    MissionTrigger,
    TriggerEvent,
    delivery_schema,
)
from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.session import SessionRecord

NOON = datetime(2026, 10, 12, 12, 0, tzinfo=UTC).timestamp()  # a Monday


@pytest.fixture(autouse=True)
def _fresh_runs_and_deliveries() -> Any:
    RunStore._MEMORY.clear()
    mission_delivery.reset_memory()
    yield
    RunStore._MEMORY.clear()
    mission_delivery.reset_memory()


def test_next_fire_is_computed_in_the_missions_timezone() -> None:
    utc = next_fire_after("0 9 * * *", "UTC", NOON)
    paris = next_fire_after("0 9 * * *", "Europe/Paris", NOON)
    assert datetime.fromtimestamp(utc, UTC).hour == 9
    assert datetime.fromtimestamp(paris, UTC).hour == 7  # CEST is UTC+2 in October
    assert utc > NOON and paris > NOON


@pytest.mark.parametrize(
    "cron, tz, message",
    [
        ("* * * * *", "UTC", "5 minutes"),
        ("*/2 * * * *", "UTC", "5 minutes"),
        ("0 9 * *", "UTC", "five-field"),
        ("0 0 9 * * *", "UTC", "five-field"),
        ("nonsense", "UTC", "five-field"),
        ("0 9 * * *", "Mars/Olympus", "timezone"),
    ],
)
def test_unsafe_or_invalid_schedules_are_refused(cron: str, tz: str, message: str) -> None:
    with pytest.raises(MissionError, match=message):
        next_fire_after(cron, tz, NOON)


def _mission(user: str = "u1", **overrides: Any) -> Mission:
    base = Mission(
        id="",
        org_id="org1",
        name="Nightly triage",
        instructions="Triage new issues",
        scope="user",
        user_id=user,
        triggers=(MissionTrigger("schedule", schedule="0 3 * * *"),),
    )
    return replace(base, **overrides)


async def test_missions_are_scoped_to_their_organization() -> None:
    store = MissionStore(None)
    mine = await store.create(_mission("u1"), {})
    await store.create(_mission("u3", org_id="org2"), {})
    assert {m.id for m in await store.all_for("org1")} == {mine.id}
    assert await store.get("org2", mine.id) is None


async def test_a_member_cannot_exceed_the_personal_mission_cap() -> None:
    store = MissionStore(None)
    for _ in range(MAX_MISSIONS_PER_USER):
        await store.create(_mission(), {})
    with pytest.raises(MissionError, match="limit"):
        await store.create(_mission(), {})
    await store.create(_mission("someone-else"), {})  # the cap is per member
    await store.create(_mission(scope="org", user_id=None), {})  # and for personal ones


async def test_disabling_clears_the_schedule_and_enabling_restores_it() -> None:
    store = MissionStore(None)
    created = await store.create(_mission(), {})
    off = await store.save(replace(created, enabled=False))
    assert off.next_run_at is None
    on = await store.save(replace(off, enabled=True))
    assert on.next_run_at is not None


async def test_only_due_enabled_missions_are_claimed_and_each_firing_moves_the_schedule() -> None:
    store = MissionStore(None)
    due = await store.create(_mission(name="due"), {})
    await store.create(_mission(name="later"), {})
    paused = await store.create(_mission(name="paused"), {})
    await store.save(replace(paused, enabled=False))
    await store.save(replace(due, next_run_at=int(time.time()) - 10))
    store._memory[due.id][0].next_run_at = int(time.time()) - 10

    claimed = await store.claim_due(None)

    assert [m.name for m in claimed] == ["due"]
    stored = await store.get("org1", due.id)
    assert (
        stored is not None and stored.next_run_at is not None and stored.next_run_at > time.time()
    )
    assert await store.claim_due(None) == []  # already claimed this firing


@pytest.mark.parametrize(
    "changes, error",
    [
        ({"deliveries": (Delivery("chat", channel="general"),)}, "Slack channel"),
        ({"deliveries": (Delivery("webhook", url="http://x.example"),)}, "https"),
        ({"deliveries": (Delivery("github_review"),)}, "GitHub trigger"),
        (
            {
                "deliveries": (
                    Delivery("webhook", url="https://x.example", output_schema={"type": "array"}),
                )
            },
            "object",
        ),
        ({"triggers": tuple(MissionTrigger("api") for _ in range(4))}, "at most 3"),
    ],
)
async def test_a_delivery_must_fit_its_mission(changes: dict[str, Any], error: str) -> None:
    with pytest.raises(MissionError, match=error):
        await MissionStore(None).create(_mission(**changes), {})


def test_the_deliver_schema_has_one_part_per_delivery_with_a_result() -> None:
    assert delivery_schema((Delivery("session"),)) is None
    schema = delivery_schema(
        (Delivery("chat", channel="C1"), Delivery("webhook", url="https://x.example"))
    )
    assert schema is not None and schema["required"] == ["chat", "webhook"]
    assert schema_problem({"chat": {"text": "hi"}, "webhook": {"summary": "ok"}}, schema) is None
    problem = schema_problem({"chat": {"text": "hi"}, "webhook": {}}, schema)
    assert problem is not None and "webhook" in problem and "summary" in problem


# --- API ----------------------------------------------------------------------------------

ORG = "missions_org"
OWNER = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "mission_owner", "X-User-Role": "member"}
OTHER = {**OWNER, "X-Tenant-User-Id": "mission_other"}
ADMIN = {**OWNER, "X-Tenant-User-Id": "mission_admin", "X-User-Role": "admin"}
BODY: dict[str, Any] = {
    "name": "Weekly deps",
    "instructions": "Check for outdated dependencies",
    "triggers": [{"source": "schedule", "schedule": "0 8 * * 1"}],
}


@pytest.fixture
def client() -> Any:
    with TestClient(app) as c:
        app.state.db_manager = None
        app.state.mission_store = MissionStore(None)
        yield c


def test_a_member_manages_their_own_missions(client: TestClient) -> None:
    created = client.post("/v1/missions", json=BODY, headers=OWNER)
    assert created.status_code == 200, created.text
    mission = created.json()
    assert mission["enabled"] and mission["next_run_at"] > time.time()
    assert (mission["scope"], mission["user_id"]) == ("user", "mission_owner")
    assert [d["kind"] for d in mission["deliveries"]] == ["session"]

    assert [m["id"] for m in client.get("/v1/missions", headers=OWNER).json()] == [mission["id"]]
    patched = client.patch(f"/v1/missions/{mission['id']}", json={"enabled": False}, headers=OWNER)
    assert patched.json()["enabled"] is False and patched.json()["next_run_at"] is None
    assert client.delete(f"/v1/missions/{mission['id']}", headers=OWNER).status_code == 200
    assert client.get("/v1/missions", headers=OWNER).json() == []


def test_another_members_mission_is_invisible_and_an_organizations_is_shared(
    client: TestClient,
) -> None:
    mission_id = client.post("/v1/missions", json=BODY, headers=OWNER).json()["id"]
    assert client.get("/v1/missions", headers=OTHER).json() == []
    assert (
        client.patch(
            f"/v1/missions/{mission_id}", json={"enabled": False}, headers=OTHER
        ).status_code
        == 404
    )
    assert client.delete(f"/v1/missions/{mission_id}", headers=OTHER).status_code == 404
    shared = client.post("/v1/missions", json={**BODY, "scope": "org"}, headers=ADMIN).json()
    assert shared["run_as"] == "", "a shared mission's service member is made at its first run"
    assert [m["id"] for m in client.get("/v1/missions", headers=OTHER).json()] == [shared["id"]]
    assert client.delete(f"/v1/missions/{shared['id']}", headers=OTHER).status_code == 403
    assert (
        client.post("/v1/missions", json={**BODY, "scope": "org"}, headers=OWNER).status_code == 403
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"triggers": [{"source": "schedule", "schedule": "* * * * *"}]},
        {"git_repos": ["acme/api", "file:///etc"]},
        {"deliveries": [{"kind": "pull_request"}]},
        {"permission_mode": "yolo"},
        {"triggers": [{"source": "schedule", "schedule": "0 8 * * 1", "timezone": "Nowhere/Land"}]},
        {"deliveries": [{"kind": "fax"}]},
    ],
)
def test_unsafe_missions_are_rejected_with_a_reason(
    client: TestClient, patch: dict[str, Any]
) -> None:
    assert client.post("/v1/missions", json={**BODY, **patch}, headers=OWNER).status_code == 422


def test_run_now_starts_a_session_and_records_the_run(client: TestClient) -> None:
    mission_id = client.post(
        "/v1/missions", json={**BODY, "permission_mode": "plan"}, headers=OWNER
    ).json()["id"]
    started = client.post(
        f"/v1/missions/{mission_id}/run", json={"text": "Only the API."}, headers=OWNER
    )
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]

    session = client.get(f"/v1/sessions/{session_id}", headers=OWNER).json()
    assert session["source"] == "scheduled"
    assert session["metadata"]["mission_id"] == mission_id
    assert session["metadata"]["permission_mode"] == "plan"
    assert client.get(f"/v1/sessions/{session_id}", headers=OTHER).status_code == 403
    runs = client.get(f"/v1/missions/{mission_id}/runs", headers=OWNER).json()
    assert [r["session_id"] for r in runs] == [session_id]
    assert client.post(f"/v1/missions/{mission_id}/run", json={}, headers=OTHER).status_code == 404


def test_a_webhook_mission_takes_signed_calls_only(client: TestClient, monkeypatch: Any) -> None:
    started: list[Any] = []

    async def start(_app: Any, mission: Any, event: Any, fallback_owner: str = "") -> Any:
        started.append(event)
        from api.services.mission_runs import RunOutcome

        return RunOutcome("created", "s1", mission.id, "run_1")

    from api.services import mission_delays

    monkeypatch.setattr(mission_delays, "start_run", start)
    created = client.post(
        "/v1/missions", json={**BODY, "triggers": [{"source": "webhook"}]}, headers=OWNER
    ).json()
    secret = created["inbound_secret"]
    assert secret and created["inbound_path"] == f"/v1/webhooks/missions/{ORG}/{created['id']}"
    assert "inbound_secret" not in client.get(f"/v1/missions/{created['id']}", headers=OWNER).json()
    body = json.dumps({"alert": "latency"}).encode()
    stamp = int(time.time())
    good = {"X-Rocket-Timestamp": str(stamp), "X-Rocket-Signature": sign(secret, stamp, body)}
    assert (
        client.post(created["inbound_path"], content=body, headers=good).json()["status"]
        == "created"
    )
    assert "latency" in started[0].body
    forged = {**good, "X-Rocket-Signature": sign("wrong", stamp, body)}
    assert client.post(created["inbound_path"], content=body, headers=forged).status_code == 401
    stale = {
        "X-Rocket-Timestamp": str(stamp - 3600),
        "X-Rocket-Signature": sign(secret, stamp - 3600, body),
    }
    assert client.post(created["inbound_path"], content=body, headers=stale).status_code == 401
    rotated = client.post(f"/v1/missions/{created['id']}/secrets/inbound", headers=OWNER).json()
    assert rotated["secret"] != secret
    assert client.post(created["inbound_path"], content=body, headers=good).status_code == 401


def test_presets_fill_a_whole_mission(client: TestClient) -> None:
    presets = {p["id"]: p for p in client.get("/v1/missions/presets", headers=OWNER).json()}
    assert {"pr-review", "ci-fixer", "issue-to-pr", "dependency-audit", "alert-triage"} <= set(
        presets
    )
    review = presets["pr-review"]["mission"]
    assert review["mode"] == "review" and review["deliveries"] == [
        {
            "kind": "github_review",
            "bot_id": "",
            "channel": "",
            "url": "",
            "output_schema": {},
            "optional": False,
        }
    ]
    saved = client.post("/v1/missions", json=review, headers=OWNER)
    assert saved.status_code == 200, saved.text


# --- delivering a run's result -------------------------------------------------------------


class _Store:
    def __init__(self, record: SessionRecord) -> None:
        self.record = record

    async def get_session(self, session_id: str, *_: Any, **__: Any) -> SessionRecord:
        return self.record

    async def mutate_session(self, session_id: str, _org: str, fn: Any) -> bool:
        fn(self.record)
        return True


def _run_app(record: SessionRecord) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        state=SimpleNamespace(
            session_store=_Store(record), db_manager=None, mission_store=MissionStore(None)
        )
    )


async def _delivering(
    deliveries: tuple[Delivery, ...],
    answer: str,
    triggers: tuple[MissionTrigger, ...] = (MissionTrigger("api"),),
) -> tuple[Any, Mission, SessionRecord]:
    mission = _mission(
        triggers=triggers,
        deliveries=(Delivery("session"), *deliveries),
        github_write=True,
    )
    schema = delivery_schema(mission.deliveries)
    record = SessionRecord(
        session_id="s1", tenant_org_id="org1", tenant_user_id="u1", title="t",
        metadata={SCHEMA_KEY: schema, "mission_run_id": "run_1"},
        conversation_history=[ChatMessage(role="assistant", content=answer)],
    )  # fmt: skip
    app = _run_app(record)
    mission = await app.state.mission_store.create(mission, {})
    await RunStore(None).create(
        MissionRun(id="run_1", org_id="org1", mission_id=mission.id, session_id="s1", source="api", started_at=1),
        {},
    )  # fmt: skip
    return app, mission, record


async def test_a_result_that_does_not_fit_is_refused_with_the_reason() -> None:
    app, _, record = await _delivering((Delivery("chat", channel="C123"),), "done")
    refused = await accept_output(app, "org1", "s1", {"chat": {"words": "hi"}})
    assert refused.startswith("Rejected:") and "text" in refused
    assert OUTPUT_KEY not in record.metadata
    accepted = await accept_output(app, "org1", "s1", {"chat": {"text": "All green."}})
    assert accepted.startswith("Result received")
    assert record.metadata[OUTPUT_KEY] == {
        "run_id": "run_1",
        "output": {"chat": {"text": "All green."}},
    }


async def test_the_platform_posts_the_result_and_records_how_it_went(monkeypatch: Any) -> None:
    sent: list[tuple[str, dict[str, Any]]] = []

    async def chat(_app: Any, _org: str, payload: dict[str, Any]) -> None:
        sent.append(("chat", payload))

    monkeypatch.setitem(mission_delivery._SENDERS, "chat", chat)
    app, mission, _ = await _delivering((Delivery("chat", channel="C123"),), "The answer.")
    await accept_output(app, "org1", "s1", {"chat": {"text": "Delivered text."}})
    assert await complete_run(app, "org1", "run_1") == "delivered"
    assert sent[0][1]["output"] == {"text": "Delivered text."}
    run = await RunStore(None).get("org1", "run_1")
    assert run is not None and run[0].status == "delivered"
    assert (await app.state.mission_store.get("org1", mission.id)).last_status == "delivered"


async def test_no_delivery_is_ever_made_from_the_runs_answer(
    monkeypatch: Any,
) -> None:
    sent: dict[str, dict[str, Any]] = {}

    def record(kind: str) -> Any:
        async def send(_app: Any, _org: str, payload: dict[str, Any]) -> None:
            sent[kind] = payload

        return send

    for kind in ("chat", "github_comment"):
        monkeypatch.setitem(mission_delivery._SENDERS, kind, record(kind))
    app, _, _ = await _delivering(
        (
            Delivery("chat", channel="C123"),
            Delivery("github_comment"),
            Delivery("webhook", url="https://hooks.example/x"),
        ),
        "The final answer.",
        triggers=(MissionTrigger("github_issue_label"),),
    )
    assert await complete_run(app, "org1", "run_1") == "delivery_failed"
    assert sent == {}, "without a deliver call, nothing is posted"
    run = await RunStore(None).get("org1", "run_1")
    assert run is not None
    results = {d["kind"]: d for d in run[0].deliveries}
    assert {kind: d["status"] for kind, d in results.items()} == {
        "chat": "failed",
        "github_comment": "failed",
        "webhook": "failed",
    }
    assert all("did not call `deliver`" in d["error"] for d in results.values())


async def test_the_agent_is_told_it_writes_a_slack_message_no_longer_than_a_channel_shows() -> None:
    from api.services.mission_runs import prompt_for

    from specifications.interfaces.automation import MAX_CHAT_CHARS

    mission = _mission(deliveries=(Delivery("session"), Delivery("chat", channel="C123")))
    prompt = prompt_for(
        mission, TriggerEvent(source="schedule", body="Daily digest"), follow_up=False
    )
    assert "A Slack message posted in a channel" in prompt
    assert "converted to Slack's format" in prompt

    app, _, _ = await _delivering((Delivery("chat", channel="C123"),), "Answer.")
    too_long = await accept_output(
        app, "org1", "s1", {"chat": {"text": "x" * (MAX_CHAT_CHARS + 1)}}
    )
    assert too_long.startswith("Rejected:") and "chat/text" in too_long


async def test_a_slack_delivery_carries_the_pull_requests_the_run_opened(monkeypatch: Any) -> None:
    from api.services.mission_records import note_pull_request

    sent: list[dict[str, Any]] = []

    async def chat(_app: Any, _org: str, payload: dict[str, Any]) -> None:
        sent.append(payload)

    monkeypatch.setitem(mission_delivery._SENDERS, "chat", chat)
    app, _, record = await _delivering(
        (Delivery("pull_request"), Delivery("chat", channel="C123")), "Done."
    )
    note_pull_request("https://github.com/acme/api/pull/12")(record)
    await accept_output(app, "org1", "s1", {"chat": {"text": "One change, see the PR."}})
    assert await complete_run(app, "org1", "run_1") == "delivered"
    assert sent[0]["pull_requests"] == ["https://github.com/acme/api/pull/12"]


async def test_a_slack_message_handed_to_deliver_is_the_one_sent(monkeypatch: Any) -> None:
    sent: list[dict[str, Any]] = []

    async def chat(_app: Any, _org: str, payload: dict[str, Any]) -> None:
        sent.append(payload)

    monkeypatch.setitem(mission_delivery._SENDERS, "chat", chat)
    app, _, _ = await _delivering(
        (Delivery("chat", channel="C123", optional=True),), "Long final answer."
    )
    await accept_output(app, "org1", "s1", {"chat": {"text": "Two upgrades are due."}})
    assert await complete_run(app, "org1", "run_1") == "delivered"
    assert [p["output"] for p in sent] == [{"text": "Two upgrades are due."}]


async def test_a_failed_delivery_is_retried_until_it_lands(monkeypatch: Any) -> None:
    calls: list[int] = []

    async def flaky(_app: Any, _org: str, _payload: dict[str, Any]) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise mission_delivery.DeliveryError("Slack is down")

    monkeypatch.setitem(mission_delivery._SENDERS, "chat", flaky)
    app, _, _ = await _delivering((Delivery("chat", channel="C123"),), "Answer.")
    await accept_output(app, "org1", "s1", {"chat": {"text": "Checks are green."}})
    assert await complete_run(app, "org1", "run_1") == "completed"
    for row in mission_delivery._memory.values():
        row["next_attempt_at"] = 0
    assert await mission_delivery.retry_due(app, None) == 1
    assert [r["status"] for r in mission_delivery._memory.values()] == ["delivered"]


async def test_a_whole_mission_turn_hands_its_result_to_deliver_and_retries_a_misfit() -> None:
    from types import SimpleNamespace

    from api.services.mission_delivery import register_deliver_tool

    from tests.turn_harness import run_turn

    schema = delivery_schema((Delivery("chat", channel="C1"),))

    def deliver_tool(registry: Any, store: Any) -> None:
        register_deliver_tool(SimpleNamespace(state=SimpleNamespace(session_store=store)), registry)

    outcome = await run_turn(
        "mission_turn",
        metadata={SCHEMA_KEY: schema, "mission_run_id": "run_9", "toolsets": ["mission"]},
        script=[
            ("deliver", {"output": {"chat": {"words": "wrong field"}}}),
            ("deliver", {"output": {"chat": {"text": "Checks are green."}}}),
        ],
        tools=deliver_tool,
    )
    results = [str(e.payload.get("result", "")) for e in outcome.events_of("tool_completed")]
    assert results[0].startswith("Rejected:") and results[1].startswith("Result received")
    assert outcome.session.metadata[OUTPUT_KEY] == {
        "run_id": "run_9",
        "output": {"chat": {"text": "Checks are green."}},
    }
    sent = [t.name for t in outcome.model.requests[0].tools or []]
    assert "deliver" in sent, "a mission run carries the deliver tool"
    plain = await run_turn("plain_turn", tools=deliver_tool)
    assert "deliver" not in [t.name for t in plain.model.requests[0].tools or []]


# --- repositories, GitHub writes, pull requests and optional deliveries ---------------------


@pytest.mark.parametrize(
    "changes, error",
    [
        ({"deliveries": (Delivery("pull_request"),)}, "needs GitHub writes"),
        ({"auto_fix": True}, "allow GitHub writes"),
        (
            {
                "mode": "review",
                "github_write": True,
                "triggers": (MissionTrigger("github_pr"),),
                "deliveries": (Delivery("pull_request"),),
            },
            "opens no pull request",
        ),
        ({"git_repos": tuple(f"acme/r{i}" for i in range(11))}, "at most 10 repositories"),
    ],
)
async def test_writes_and_pull_requests_must_fit_the_mission(
    changes: dict[str, Any], error: str
) -> None:
    with pytest.raises(MissionError, match=error):
        await MissionStore(None).create(_mission(**changes), {})


async def test_a_mission_keeps_its_repositories_once_each_and_its_writes() -> None:
    created = await MissionStore(None).create(
        _mission(
            git_repos=("acme/api", "acme/web", "acme/api"),
            github_write=True,
            deliveries=(Delivery("pull_request", optional=True),),
        ),
        {},
    )
    assert created.git_repos == ("acme/api", "acme/web")
    assert created.github_write is True
    assert [(d.kind, d.optional) for d in created.deliveries] == [
        ("session", False),
        ("pull_request", True),
    ]


def test_an_optional_delivery_is_not_required_and_a_pull_request_needs_no_result() -> None:
    assert delivery_schema((Delivery("session"), Delivery("pull_request"))) is None
    schema = delivery_schema(
        (
            Delivery("chat", channel="C1", optional=True),
            Delivery("webhook", url="https://x.example"),
        )
    )
    assert schema is not None and schema["required"] == ["webhook"]
    assert schema_problem({"webhook": {"summary": "ok"}}, schema) is None


def test_the_api_takes_repositories_and_writes_and_gives_them_back(client: TestClient) -> None:
    saved = client.post(
        "/v1/missions",
        json={
            **BODY,
            "git_repos": ["https://github.com/acme/api", "acme/web"],
            "github_write": True,
            "deliveries": [{"kind": "pull_request", "optional": True}],
        },
        headers=OWNER,
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["git_repos"] == ["acme/api", "acme/web"]
    assert body["github_write"] is True
    assert {"kind": "pull_request", "optional": True}.items() <= body["deliveries"][-1].items()


def test_a_run_names_every_repository_and_what_it_must_leave_behind() -> None:
    from api.services.mission_runs import prompt_for

    event = TriggerEvent(source="schedule", body="Weekly upgrade")
    writer = _mission(
        git_repos=("acme/api", "acme/web"),
        git_branch="develop",
        github_write=True,
        deliveries=(Delivery("session"), Delivery("pull_request", optional=True)),
    )
    prompt = prompt_for(writer, event, follow_up=False)
    assert "`acme/api` (branch `develop`), `acme/web`" in prompt
    assert "open it with `git_open_pr`" in prompt and "nothing to change, open none" in prompt
    assert "read-only" not in prompt

    reader = _mission(git_repos=("acme/api",))
    prompt = prompt_for(reader, event, follow_up=False)
    assert "Repository for this task: `acme/api`" in prompt
    assert "GitHub is read-only for this run" in prompt

    pushed = TriggerEvent(source="github_pr", repository="acme/web", number=4, trusted=True)
    assert "`acme/web` (branch `develop`), `acme/api`." in prompt_for(
        writer, pushed, follow_up=False
    )


async def test_an_optional_delivery_with_nothing_to_send_is_skipped(monkeypatch: Any) -> None:
    sent: list[dict[str, Any]] = []

    async def chat(_app: Any, _org: str, payload: dict[str, Any]) -> None:
        sent.append(payload)

    monkeypatch.setitem(mission_delivery._SENDERS, "chat", chat)
    app, _, _ = await _delivering((Delivery("chat", channel="C123", optional=True),), "Nothing.")
    assert await complete_run(app, "org1", "run_1") == "completed"
    assert sent == [], "a Slack message is sent only from a deliver call"
    run = await RunStore(None).get("org1", "run_1")
    assert run is not None and [d["status"] for d in run[0].deliveries] == ["skipped"]


@pytest.mark.parametrize(
    "optional, opened, run_status, delivery_status",
    [
        (False, True, "delivered", "delivered"),
        (False, False, "delivery_failed", "failed"),
        (True, False, "completed", "skipped"),
    ],
)
async def test_a_pull_request_delivery_settles_on_the_pull_request_the_run_opened(
    optional: bool, opened: bool, run_status: str, delivery_status: str
) -> None:
    from api.services.mission_records import note_pull_request

    app, _, record = await _delivering(
        (Delivery("pull_request", optional=optional),), "Opened the upgrade."
    )
    if opened:
        note_pull_request("https://github.com/acme/api/pull/7")(record)
    assert await complete_run(app, "org1", "run_1") == run_status
    run = await RunStore(None).get("org1", "run_1")
    assert run is not None
    (result,) = run[0].deliveries
    assert result["status"] == delivery_status
    assert result.get("url") == ("https://github.com/acme/api/pull/7" if opened else None)


def test_a_pull_request_counts_only_for_the_run_that_opened_it() -> None:
    from api.services.mission_records import note_pull_request, pull_requests_of

    record = SessionRecord(
        session_id="s1", tenant_org_id="org1", tenant_user_id="u1", title="t",
        metadata={"mission_run_id": "run_1"},
    )  # fmt: skip
    note_pull_request("https://github.com/acme/api/pull/7")(record)
    note_pull_request("https://github.com/acme/api/pull/7")(record)
    assert pull_requests_of(record.metadata, "run_1") == ["https://github.com/acme/api/pull/7"]
    record.metadata["mission_run_id"] = "run_2"
    assert pull_requests_of(record.metadata, "run_2") == []
    note_pull_request("https://github.com/acme/api/pull/8")(record)
    assert pull_requests_of(record.metadata, "run_2") == ["https://github.com/acme/api/pull/8"]


@pytest.mark.parametrize(
    "name, arguments, changes",
    [
        ("git_push", {}, True),
        ("git_open_pr", {"title": "t", "body": "b"}, True),
        ("github_pull_request", {"action": "view"}, False),
        ("github_pull_request", {"action": "comment"}, True),
        ("github_api", {"method": "GET", "path": "/repos/acme/api"}, False),
        ("github_api", {"method": "POST", "path": "/repos/acme/api/issues"}, True),
        ("git_commit", {"message": "m"}, False),
    ],
)
def test_forge_changes_are_told_from_reads(
    name: str, arguments: dict[str, Any], changes: bool
) -> None:
    from agent_core.permissions import changes_forge

    assert changes_forge(name, arguments) is changes


async def test_a_run_without_github_writes_is_refused_every_change_on_github() -> None:
    from api.services.mission_records import GITHUB_WRITE_KEY

    from specifications.interfaces.git import PublishResult, PublishSpec
    from tests.turn_harness import run_turn

    class Engine:
        pushed = 0

        async def publish_branch(self, session_id: str, spec: PublishSpec) -> PublishResult:
            Engine.pushed += 1
            return PublishResult("acme/api", "feat/x", "abc123def4567", "git_push", commits=1)

    reader = await run_turn(
        "mission_reader",
        metadata={GITHUB_WRITE_KEY: False, "permission_mode": "auto"},
        script=[("git_push", {}), ("github_pull_request", {"action": "comment", "repo": "a/b"})],
        git_engine=Engine(),
    )
    results = [str(e.payload.get("result", "")) for e in reader.events_of("tool_completed")]
    assert len(results) == 2 and all("may only read it" in r for r in results)
    assert Engine.pushed == 0

    writer = await run_turn(
        "mission_writer",
        metadata={GITHUB_WRITE_KEY: True, "permission_mode": "auto"},
        script=[("git_push", {})],
        git_engine=Engine(),
    )
    results = [str(e.payload.get("result", "")) for e in writer.events_of("tool_completed")]
    assert results and "may only read it" not in results[0]
    assert Engine.pushed == 1


@pytest.mark.setting("slack.outbound_channels")
def test_a_star_in_the_outbound_channels_lets_a_mission_report_to_any_channel(
    client: TestClient,
) -> None:
    body = {**BODY, "deliveries": [{"kind": "chat", "channel": "C0ANYWHERE"}]}
    reset_memory_settings()
    client.portal.call(  # type: ignore[union-attr]
        save_org_config, None, ORG, "slack", {"outbound_channels": ["C0LISTED"]}, []
    )
    refused = client.post("/v1/missions", json=body, headers=OWNER)
    client.portal.call(  # type: ignore[union-attr]
        save_org_config, None, ORG, "slack", {"outbound_channels": ["*"]}, []
    )
    accepted = client.post("/v1/missions", json=body, headers=OWNER)
    reset_memory_settings()

    assert refused.status_code == 422 and "outbound channels" in refused.text
    assert accepted.status_code == 200, accepted.text
