"""Start-up wiring: persistence fallback, periodic tasks and the shared services."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from api.bootstrap import background
from api.bootstrap.background import BackgroundServices, _refresh_one, _run_periodically
from api.bootstrap.core import assemble_core
from api.bootstrap.persistence import open_persistence
from api.bootstrap.runtime import cluster_bus_enabled
from api.store import InMemorySessionStore
from fastapi import FastAPI
from llm_gateway.stub import STUB_ENV_FLAG


async def test_without_a_database_the_store_lives_in_memory() -> None:
    persistence = await open_persistence(None)

    assert isinstance(persistence.session_store, InMemorySessionStore)
    assert persistence.db_manager is None


async def test_an_unreachable_database_falls_back_to_memory_outside_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    persistence = await open_persistence("postgresql+asyncpg://nobody:x@127.0.0.1:1/none")

    assert isinstance(persistence.session_store, InMemorySessionStore)
    assert persistence.db_manager is None


async def test_production_refuses_to_start_without_its_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(Exception):  # noqa: B017 - the driver's connection error, unwrapped
        await open_persistence("postgresql+asyncpg://nobody:x@127.0.0.1:1/none")


def test_the_cluster_bus_is_only_on_when_postgres_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROCKET_EVENT_BUS", raising=False)
    assert cluster_bus_enabled() is False
    monkeypatch.setenv("ROCKET_EVENT_BUS", " Postgres ")
    assert cluster_bus_enabled() is True


async def test_a_failing_round_is_logged_and_the_loop_keeps_going(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rounds = 0
    third_round = asyncio.Event()

    async def action() -> None:
        nonlocal rounds
        rounds += 1
        if rounds == 3:
            third_round.set()
        if rounds == 1:
            raise RuntimeError("boom")

    with caplog.at_level(logging.WARNING):
        task = asyncio.create_task(_run_periodically("Sweep", 0.001, action))
        await asyncio.wait_for(third_round.wait(), timeout=2)
        task.cancel()
        await task

    assert "Sweep error: boom" in caplog.text
    assert task.done() and not task.cancelled()


async def test_run_immediately_acts_before_the_first_wait() -> None:
    calls: list[str] = []

    async def action() -> None:
        calls.append("ran")

    task = asyncio.create_task(_run_periodically("Poll", 3600, action, run_immediately=True))
    await asyncio.sleep(0.05)
    task.cancel()
    await task

    assert calls == ["ran"]


async def test_waiting_loops_do_not_act_before_their_first_interval() -> None:
    calls: list[str] = []

    async def action() -> None:
        calls.append("ran")

    task = asyncio.create_task(_run_periodically("Sweep", 3600, action))
    await asyncio.sleep(0.05)
    task.cancel()
    await task

    assert calls == []


@pytest.fixture
def fake_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEFAULT_SANDBOX_DRIVER", "fake")
    monkeypatch.setenv(STUB_ENV_FLAG, "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("ROCKET_EVENT_BUS", raising=False)


async def test_core_assembly_publishes_the_shared_services(fake_runtime: None) -> None:
    app = FastAPI()

    core = await assemble_core(app)

    assert app.state.orchestrator is core.orchestrator
    assert app.state.session_store is core.session_store
    assert app.state.event_bus is core.event_bus
    assert app.state.global_concurrency_gate is core.concurrency_gate
    assert app.state.session_write_locks is core.session_locks
    assert app.state.draining is False
    assert core.db_manager is None and not hasattr(app.state, "db_manager")
    assert core.cluster_dsn is None
    assert core.orchestrator.human_wait_scope == core.concurrency_gate.suspended


async def test_background_services_start_and_stop_without_a_database(
    fake_runtime: None,
) -> None:
    app = FastAPI()
    core = await assemble_core(app)
    services = BackgroundServices(app, core)

    services.start()
    await asyncio.wait_for(services.stop(), timeout=2)

    assert services._tasks == []


async def test_a_reachable_mcp_server_is_refreshed_and_an_unreachable_one_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def probe(**kwargs: Any) -> tuple[str, list[dict[str, Any]], str | None]:
        if kwargs["endpoint_or_command"] == "https://down.test":
            raise RuntimeError("unreachable")
        return "connected", [{"name": "search"}], None

    monkeypatch.setattr(background, "probe_and_fetch_tools", probe)
    cipher = SimpleNamespace()
    monkeypatch.setattr(background, "auth_headers", lambda auth, _cipher: {})

    def server(endpoint: str) -> Any:
        return SimpleNamespace(
            id="s",
            org_id="o",
            transport="http",
            endpoint_or_command=endpoint,
            encrypted_auth={},
            status="unknown",
            last_probed=None,
            tools=[],
        )

    up, down = server("https://up.test"), server("https://down.test")
    await _refresh_one(up, cipher)  # type: ignore[arg-type]
    await _refresh_one(down, cipher)  # type: ignore[arg-type]

    assert up.status == "connected" and up.tools == [{"name": "search"}] and up.last_probed
    assert down.status == "unknown" and down.last_probed is None
