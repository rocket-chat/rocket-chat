"""Scheduled background missions: schedule rules, store, API and launch."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest
from api.main import app
from api.services.missions import (
    MAX_MISSIONS_PER_USER,
    Mission,
    MissionError,
    MissionStore,
    claim_due,
    next_fire_after,
)
from fastapi.testclient import TestClient

NOON = datetime(2026, 10, 12, 12, 0, tzinfo=UTC).timestamp()  # a Monday


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


def test_the_shortest_allowed_interval_is_five_minutes() -> None:
    assert next_fire_after("*/5 * * * *", "UTC", NOON) > NOON


def _mission(user: str = "u1", **overrides: object) -> Mission:
    base = {
        "id": "",
        "org_id": "org1",
        "user_id": user,
        "name": "Nightly triage",
        "prompt": "Triage new issues",
        "cron": "0 3 * * *",
    }
    return Mission(**{**base, **overrides})  # type: ignore[arg-type]


async def test_missions_are_scoped_to_their_organization_and_owner() -> None:
    store = MissionStore(None)
    mine = await store.create(_mission("u1"))
    theirs = await store.create(_mission("u2"))
    await store.create(_mission("u3", org_id="org2"))

    assert {m.id for m in await store.list("org1", "u1")} == {mine.id}
    assert {m.id for m in await store.list("org1", None)} == {mine.id, theirs.id}
    assert await store.get("org2", mine.id) is None


async def test_a_member_cannot_exceed_the_mission_cap() -> None:
    store = MissionStore(None)
    for _ in range(MAX_MISSIONS_PER_USER):
        await store.create(_mission())
    with pytest.raises(MissionError, match="limit"):
        await store.create(_mission())
    await store.create(_mission("someone-else"))  # the cap is per member


async def test_disabling_clears_the_schedule_and_enabling_restores_it() -> None:
    store = MissionStore(None)
    created = await store.create(_mission())
    off = await store.update("org1", created.id, enabled=False)
    assert off is not None and off.next_run_at is None
    on = await store.update("org1", created.id, enabled=True)
    assert on is not None and on.next_run_at is not None


async def test_only_due_enabled_missions_are_claimed_and_each_firing_moves_the_schedule() -> None:
    store = MissionStore(None)
    due = await store.create(_mission(name="due"))
    later = await store.create(_mission(name="later"))
    paused = await store.create(_mission(name="paused"))
    await store.update("org1", paused.id, enabled=False)
    due.next_run_at = int(time.time()) - 10

    claimed = await claim_due(None, store)

    assert [m.name for m in claimed] == ["due"]
    assert due.next_run_at is not None and due.next_run_at > time.time()
    assert later.next_run_at is not None
    assert await claim_due(None, store) == []  # already claimed this firing


# --- API ----------------------------------------------------------------------------------

OWNER = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "mission_owner",
    "X-User-Role": "developer",
}
OTHER = {**OWNER, "X-Tenant-User-Id": "mission_other"}
ADMIN = {**OWNER, "X-Tenant-User-Id": "mission_admin", "X-User-Role": "admin"}
BODY = {"name": "Weekly deps", "prompt": "Check for outdated dependencies", "cron": "0 8 * * 1"}


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        app.state.db_manager = None
        app.state.mission_store = MissionStore(None)
        yield c


def test_a_member_manages_their_own_missions(client: TestClient) -> None:
    created = client.post("/v1/missions", json=BODY, headers=OWNER)
    assert created.status_code == 200, created.text
    mission = created.json()
    assert mission["enabled"] and mission["next_run_at"] > time.time()

    assert [m["id"] for m in client.get("/v1/missions", headers=OWNER).json()] == [mission["id"]]
    patched = client.patch(
        f"/v1/missions/{mission['id']}", json={"enabled": False}, headers=OWNER
    ).json()
    assert patched["enabled"] is False and patched["next_run_at"] is None
    assert client.delete(f"/v1/missions/{mission['id']}", headers=OWNER).status_code == 200
    assert client.get("/v1/missions", headers=OWNER).json() == []


def test_another_members_mission_is_invisible_but_an_admin_can_manage_it(
    client: TestClient,
) -> None:
    mission_id = client.post("/v1/missions", json=BODY, headers=OWNER).json()["id"]

    assert client.get("/v1/missions", headers=OTHER).json() == []
    assert (
        client.patch(f"/v1/missions/{mission_id}", json={"name": "x"}, headers=OTHER).status_code
        == 404
    )
    assert client.delete(f"/v1/missions/{mission_id}", headers=OTHER).status_code == 404
    assert [m["id"] for m in client.get("/v1/missions?all=true", headers=ADMIN).json()] == [
        mission_id
    ]
    assert client.get("/v1/missions?all=true", headers=OTHER).json() == []
    assert client.delete(f"/v1/missions/{mission_id}", headers=ADMIN).status_code == 200


@pytest.mark.parametrize(
    "patch",
    [
        {"cron": "* * * * *"},
        {"git_repo": "file:///etc"},
        {"permission_mode": "yolo"},
        {"timezone": "Nowhere/Land"},
    ],
)
def test_unsafe_missions_are_rejected_with_a_reason(
    client: TestClient, patch: dict[str, str]
) -> None:
    assert client.post("/v1/missions", json={**BODY, **patch}, headers=OWNER).status_code == 422


def test_run_now_starts_an_owned_scheduled_session(client: TestClient) -> None:
    mission_id = client.post(
        "/v1/missions", json={**BODY, "permission_mode": "plan"}, headers=OWNER
    ).json()["id"]
    started = client.post(f"/v1/missions/{mission_id}/run", headers=OWNER)
    assert started.status_code == 200
    session_id = started.json()["session_id"]

    session = client.get(f"/v1/sessions/{session_id}", headers=OWNER).json()
    assert session["source"] == "scheduled"
    assert session["metadata"]["mission_id"] == mission_id
    assert session["metadata"]["permission_mode"] == "plan"
    assert client.get(f"/v1/sessions/{session_id}", headers=OTHER).status_code == 403

    listed = client.get("/v1/missions", headers=OWNER).json()[0]
    assert listed["last_session_id"] == session_id and listed["last_status"] == "started"
    assert client.post(f"/v1/missions/{mission_id}/run", headers=OTHER).status_code == 404
