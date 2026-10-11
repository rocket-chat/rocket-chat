"""Tracking background processes: the table mirror, events, the agent's note and the routes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from agent_core.tools import processes
from api.events import AsyncIOEventBus
from api.main import app
from api.services.process_tracker import (
    ProcessStore,
    ProcessTracker,
    TrackedProcess,
    describe_for_agent,
    unreported_exits,
)
from fastapi.testclient import TestClient
from test_background_processes import _LocalShell

from specifications.interfaces.events import EventType
from specifications.interfaces.sandbox import SandboxStatus

SESSION = "session_proc"
ORG = "proc_org"


class _Sandbox(_LocalShell):
    """A local shell that also answers the lifecycle questions the tracker asks."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.running = True
        self.holds: list[tuple[str, float]] = []

    async def get_status(self, session_id: str) -> SandboxStatus:
        return SandboxStatus.RUNNING if self.running else SandboxStatus.HIBERNATED

    def hold_awake(self, session_id: str, seconds: float) -> None:
        self.holds.append((session_id, seconds))


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Sandbox:
    monkeypatch.setattr(processes, "PROC_ROOT", str(tmp_path / "proc"))
    monkeypatch.setattr(processes, "STOP_GRACE_SECONDS", 0)
    (tmp_path / "work").mkdir()
    monkeypatch.setattr(ProcessStore, "_MEMORY", {})
    return _Sandbox(tmp_path / "work")


def _tracker() -> tuple[ProcessTracker, AsyncIOEventBus]:
    bus = AsyncIOEventBus()
    return ProcessTracker(ProcessStore(None), bus), bus


def _kinds(bus: AsyncIOEventBus) -> list[str]:
    events, _ = bus.replay_since(SESSION, 0)
    return [f"{e.event_type.value}:{e.payload['state']}" for e in events]


async def test_start_and_exit_are_recorded_once_and_announced(sandbox: _Sandbox) -> None:
    tracker, bus = _tracker()
    await processes.start_process(sandbox, SESSION, "sleep 1; exit 4", name="build")

    first = await tracker.refresh(SESSION, ORG, sandbox)
    assert [(p.name, p.state) for p in first] == [("build", "running")]

    await asyncio.sleep(1.6)
    after = await tracker.refresh(SESSION, ORG, sandbox)
    again = await tracker.refresh(SESSION, ORG, sandbox)
    assert [(p.state, p.exit_code) for p in after] == [("exited", 4)]
    assert again == after
    assert _kinds(bus) == ["process_started:running", "process_exited:exited"]


async def test_history_survives_when_the_sandbox_is_gone(sandbox: _Sandbox) -> None:
    tracker, _ = _tracker()
    started = await processes.start_process(sandbox, SESSION, "sleep 30")
    await tracker.refresh(SESSION, ORG, sandbox)
    sandbox.running = False

    kept = await tracker.refresh(SESSION, ORG, sandbox)
    assert [p.state for p in kept] == ["running"], "no sandbox, no news: the last state stays"
    await processes.stop_process(
        _Sandbox(sandbox.root), SESSION, next(w for w in started.split() if w.startswith("p"))
    )


async def test_a_vanished_running_process_becomes_lost(sandbox: _Sandbox) -> None:
    tracker, bus = _tracker()
    await ProcessStore(None).save(
        TrackedProcess(SESSION, "pdeadbe", ORG, "ghost", "sleep 1", "running", started_at=1)
    )
    result = await tracker.refresh(SESSION, ORG, sandbox)
    assert [p.state for p in result] == ["lost"]
    assert _kinds(bus) == ["process_exited:lost"]


async def test_a_running_process_holds_the_sandbox_awake_and_the_hold_is_released(
    sandbox: _Sandbox,
) -> None:
    tracker, _ = _tracker()
    started = await processes.start_process(sandbox, SESSION, "sleep 30")
    process_id = next(w for w in started.split() if w.startswith("p"))
    await tracker.refresh(SESSION, ORG, sandbox, keepalive_seconds=600)
    await processes.stop_process(sandbox, SESSION, process_id)
    await tracker.refresh(SESSION, ORG, sandbox, keepalive_seconds=600)
    assert sandbox.holds == [(SESSION, 600), (SESSION, 0)]


def test_the_agent_hears_about_each_exit_once() -> None:
    running = TrackedProcess(SESSION, "p1", ORG, "dev", "npm run dev", "running", port=3000)
    crashed = TrackedProcess(SESSION, "p2", ORG, "tests", "pytest", "exited", exit_code=1)
    note = describe_for_agent([running, crashed], set())
    assert note is not None
    assert "p1 running, port 3000" in note and "p2 exited with code 1 while you were away" in note

    reported = unreported_exits([running, crashed], set())
    assert reported == {"p2"}
    later = describe_for_agent([running, crashed], reported)
    assert later is not None and "p2" not in later
    assert describe_for_agent([crashed], reported) is None


# --- routes --------------------------------------------------------------------------------

HEADERS = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "proc_user",
    "X-User-Role": "developer",
}


@pytest.fixture
def client(sandbox: _Sandbox) -> Any:
    with TestClient(app) as c:
        app.state.db_manager = None
        app.state.sandbox_driver = sandbox
        yield c


def _new_session(client: TestClient) -> str:
    return str(
        client.post("/v1/sessions", json={"title": "p"}, headers=HEADERS).json()["session_id"]
    )


async def _start(sandbox: _Sandbox, session_id: str, command: str) -> str:
    message = await processes.start_process(sandbox, session_id, command, port=8080)
    return next(w for w in message.split() if w.startswith("p"))


def test_the_routes_list_read_and_stop(client: TestClient, sandbox: _Sandbox) -> None:
    session_id = _new_session(client)
    process_id = asyncio.run(_start(sandbox, session_id, "echo hello-from-proc; sleep 30"))

    listed = client.get(f"/v1/sessions/{session_id}/processes", headers=HEADERS).json()
    assert listed["sandbox_running"] is True
    assert [(p["process_id"], p["state"], p["port"]) for p in listed["processes"]] == [
        (process_id, "running", 8080)
    ]

    out = client.get(f"/v1/sessions/{session_id}/processes/{process_id}/output", headers=HEADERS)
    assert "hello-from-proc" in out.json()["output"]

    stopped = client.post(f"/v1/sessions/{session_id}/processes/{process_id}/stop", headers=HEADERS)
    assert stopped.status_code == 200
    after = client.get(f"/v1/sessions/{session_id}/processes", headers=HEADERS).json()
    assert after["processes"][0]["state"] == "stopped"


def test_the_routes_refuse_strangers_bad_ids_and_read_only_writers(
    client: TestClient, sandbox: _Sandbox
) -> None:
    session_id = _new_session(client)
    other_user = {**HEADERS, "X-Tenant-User-Id": "someone_else"}
    assert client.get(f"/v1/sessions/{session_id}/processes", headers=other_user).status_code == 403
    assert (
        client.get(
            f"/v1/sessions/{session_id}/processes/..%2Fetc/output", headers=HEADERS
        ).status_code
        == 404
    )
    shared = {**HEADERS, "X-User-Role": "readonly"}
    assert (
        client.post(f"/v1/sessions/{session_id}/processes/pabcdef/stop", headers=shared).status_code
        == 403
    )
    assert client.get("/v1/sessions/nope/processes", headers=HEADERS).status_code == 404


def test_without_a_running_sandbox_the_history_is_returned(
    client: TestClient, sandbox: _Sandbox
) -> None:
    session_id = _new_session(client)
    sandbox.running = False
    body = client.get(f"/v1/sessions/{session_id}/processes", headers=HEADERS).json()
    assert body == {"processes": [], "sandbox_running": False}
    assert (
        client.get(
            f"/v1/sessions/{session_id}/processes/pabcdef/output", headers=HEADERS
        ).status_code
        == 409
    )


def test_process_events_are_part_of_the_event_vocabulary() -> None:
    assert EventType.PROCESS_STARTED.value == "process_started"
    assert EventType.PROCESS_EXITED.value == "process_exited"
