"""Delayed missions: a trigger on an event or the webhook may wait before its run starts; the
waiting run is kept, listed, started once when due, and dropped if its mission is off or gone."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from api.main import app
from api.services import mission_delays
from api.services.mission_delays import (
    DelayedRunStore,
    event_from_json,
    event_json,
    start_due_runs,
    start_or_delay,
)
from api.services.mission_records import RunStore
from api.services.mission_runs import RunOutcome
from api.services.missions import MissionError, MissionStore, validate
from api.services.outbound_webhooks import sign
from fastapi.testclient import TestClient

from specifications.interfaces.automation import (
    MAX_DELAYED_RUNS_PER_MISSION,
    Mission,
    MissionTrigger,
    TriggerEvent,
    TriggerFilter,
    trigger_delay,
)

ORG = "delays_org"


@pytest.fixture(autouse=True)
def _fresh() -> Any:
    DelayedRunStore._MEMORY.clear()
    RunStore._MEMORY.clear()
    yield
    DelayedRunStore._MEMORY.clear()
    RunStore._MEMORY.clear()


def _mission(*triggers: MissionTrigger, **overrides: Any) -> Mission:
    base = Mission(
        id="mission_d1",
        org_id=ORG,
        name="Post-deploy watcher",
        instructions="Check the logs",
        scope="user",
        user_id="u1",
        triggers=triggers or (MissionTrigger("webhook", delay_minutes=60),),
    )
    return replace(base, **overrides)


def _event(source: str = "webhook", **overrides: Any) -> TriggerEvent:
    return TriggerEvent(
        **{"title": "deploy", "body": "omega", "trusted": True, **overrides}, source=source
    )


def _recording_start(started: list[tuple[str, TriggerEvent, str]]) -> Any:
    async def start(
        _app: Any, mission: Mission, event: TriggerEvent, fallback_owner: str = ""
    ) -> Any:
        started.append((mission.id, event, fallback_owner))
        return RunOutcome("created", "s1", mission.id, "run_1")

    return start


# --- rules -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "trigger, error",
    [
        (MissionTrigger("schedule", schedule="0 3 * * *", delay_minutes=10), "event or a webhook"),
        (MissionTrigger("api", delay_minutes=10), "event or a webhook"),
        (MissionTrigger("webhook", delay_minutes=1441), "1440"),
        (MissionTrigger("github_pr", delay_minutes=-1), "1440"),
    ],
)
def test_only_events_wait_and_never_more_than_a_day(trigger: MissionTrigger, error: str) -> None:
    with pytest.raises(MissionError, match=error):
        validate(_mission(trigger))


def test_an_event_and_the_webhook_may_wait() -> None:
    mission = validate(
        _mission(
            MissionTrigger("webhook", delay_minutes=60),
            MissionTrigger("github_pr", delay_minutes=1440),
        )
    )
    assert [t.delay_minutes for t in mission.triggers] == [60, 1440]


def test_the_shortest_delay_of_the_triggers_that_take_the_event_wins() -> None:
    triggers = (
        MissionTrigger("github_pr", TriggerFilter(repositories=("acme/api",)), delay_minutes=30),
        MissionTrigger("github_pr", delay_minutes=90),
        MissionTrigger("webhook", delay_minutes=5),
    )
    assert trigger_delay(triggers, _event("github_pr", repository="acme/api")) == 30 * 60
    assert trigger_delay(triggers, _event("github_pr", repository="acme/web")) == 90 * 60
    assert trigger_delay(triggers, _event("github_workflow_failed")) == 0


def test_an_event_is_kept_as_it_was_received() -> None:
    event = _event("github_pr", repository="acme/api", labels=("ai-fix", "urgent"), number=7)
    assert event_from_json(json.loads(json.dumps(event_json(event)))) == event
    assert event_from_json({**event_json(event), "from_a_newer_version": 1}) == event


# --- starting later -------------------------------------------------------------------------


async def test_a_delayed_trigger_keeps_the_event_instead_of_starting(monkeypatch: Any) -> None:
    started: list[Any] = []
    monkeypatch.setattr(mission_delays, "start_run", _recording_start(started))
    app_ = SimpleNamespace(state=SimpleNamespace(db_manager=None))

    before = int(time.time())
    outcome = await start_or_delay(app_, _mission(), _event(), fallback_owner="alice")

    assert outcome.status == "scheduled" and started == []
    assert (
        outcome.starts_at is not None and before + 3600 <= outcome.starts_at <= time.time() + 3600
    )
    [waiting] = await DelayedRunStore(None).of_mission(ORG, "mission_d1")
    assert (waiting.event.body, waiting.fallback_owner) == ("omega", "alice")


async def test_a_trigger_without_delay_starts_at_once(monkeypatch: Any) -> None:
    started: list[Any] = []
    monkeypatch.setattr(mission_delays, "start_run", _recording_start(started))
    app_ = SimpleNamespace(state=SimpleNamespace(db_manager=None))

    outcome = await start_or_delay(app_, _mission(MissionTrigger("webhook")), _event())

    assert outcome.status == "created" and len(started) == 1
    assert await DelayedRunStore(None).of_mission(ORG, "mission_d1") == []


async def test_a_mission_keeps_a_bounded_number_of_waiting_runs(monkeypatch: Any) -> None:
    monkeypatch.setattr(mission_delays, "start_run", _recording_start([]))
    app_ = SimpleNamespace(state=SimpleNamespace(db_manager=None))
    for _ in range(MAX_DELAYED_RUNS_PER_MISSION):
        assert (await start_or_delay(app_, _mission(), _event())).status == "scheduled"

    refused = await start_or_delay(app_, _mission(), _event())

    assert refused.status == "ignored" and "too many runs" in refused.reason


async def test_a_due_run_starts_once_with_its_event_and_owner(monkeypatch: Any) -> None:
    started: list[Any] = []
    monkeypatch.setattr(mission_delays, "start_run", _recording_start(started))
    missions = MissionStore(None)
    mission = await missions.create(_mission(), {})
    app_ = SimpleNamespace(state=SimpleNamespace(db_manager=None, mission_store=missions))
    store = DelayedRunStore(None)
    now = int(time.time())
    await store.add(mission, _event(body="due"), "alice", now - 1)
    await store.add(mission, _event(body="later"), "bob", now + 600)

    await start_due_runs(app_, None)
    await start_due_runs(app_, None)

    assert [(m, e.body, owner) for m, e, owner in started] == [(mission.id, "due", "alice")]
    assert [w.event.body for w in await store.of_mission(ORG, mission.id)] == ["later"]


async def test_a_due_run_of_a_mission_switched_off_or_deleted_starts_nothing(
    monkeypatch: Any,
) -> None:
    started: list[Any] = []
    monkeypatch.setattr(mission_delays, "start_run", _recording_start(started))
    missions = MissionStore(None)
    off = await missions.create(_mission(name="off"), {})
    await missions.save(replace(off, enabled=False))
    gone = await missions.create(_mission(name="gone"), {})
    app_ = SimpleNamespace(state=SimpleNamespace(db_manager=None, mission_store=missions))
    store = DelayedRunStore(None)
    past = int(time.time()) - 1
    await store.add(off, _event(), "", past)
    await store.add(gone, _event(), "", past)
    await missions.delete(ORG, gone.id)

    await start_due_runs(app_, None)

    assert started == []
    assert await store.claim_due(None) == [], "a dropped run is not kept for later"


# --- API -------------------------------------------------------------------------------------

OWNER = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "delay_owner", "X-User-Role": "member"}
BODY: dict[str, Any] = {
    "name": "Post-deploy watcher",
    "instructions": "Check the logs an hour after the deploy",
    "triggers": [{"source": "webhook", "delay_minutes": 60}],
}


@pytest.fixture
def client() -> Any:
    with TestClient(app) as c:
        app.state.db_manager = None
        app.state.mission_store = MissionStore(None)
        yield c


def test_a_delayed_webhook_answers_when_its_run_starts_and_lists_it(
    client: TestClient, monkeypatch: Any
) -> None:
    started: list[Any] = []
    monkeypatch.setattr(mission_delays, "start_run", _recording_start(started))
    created = client.post("/v1/missions", json=BODY, headers=OWNER)
    assert created.status_code == 200, created.text
    mission = created.json()
    assert mission["triggers"][0]["delay_minutes"] == 60
    body = json.dumps({"environment": "omega"}).encode()
    stamp = int(time.time())
    signed = {
        "X-Rocket-Timestamp": str(stamp),
        "X-Rocket-Signature": sign(mission["inbound_secret"], stamp, body),
    }

    answer = client.post(mission["inbound_path"], content=body, headers=signed).json()

    assert answer["status"] == "scheduled" and started == []
    assert answer["starts_at"] >= stamp + 3600
    runs = client.get(f"/v1/missions/{mission['id']}/runs", headers=OWNER).json()
    assert [(r["status"], r["source"], r["started_at"]) for r in runs] == [
        ("scheduled", "webhook", answer["starts_at"])
    ]


@pytest.mark.parametrize(
    "trigger",
    [
        {"source": "schedule", "schedule": "0 8 * * 1", "delay_minutes": 30},
        {"source": "webhook", "delay_minutes": 2000},
    ],
)
def test_a_delay_the_mission_cannot_have_is_refused(
    client: TestClient, trigger: dict[str, Any]
) -> None:
    assert (
        client.post("/v1/missions", json={**BODY, "triggers": [trigger]}, headers=OWNER).status_code
        == 422
    )
