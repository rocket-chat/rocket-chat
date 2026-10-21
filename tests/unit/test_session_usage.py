"""What a session cost, in total, per turn and per model."""

from __future__ import annotations

from typing import Any

import pytest
from api.main import app
from api.services.session_usage import TurnWindow, summarize_session_usage
from api.services.usage import UsageLedger
from fastapi.testclient import TestClient

from specifications.interfaces.llm import UsageEvent


def _event(at: int, *, model: str = "m/a", prompt: int = 1000, cached: int = 0, out: int = 100,
           cost: float = 0.01, key: str = "platform") -> dict[str, Any]:  # fmt: skip
    return {
        "model": model,
        "prompt_tokens": prompt,
        "completion_tokens": out,
        "cache_read_tokens": cached,
        "cache_creation_tokens": 0,
        "cost_usd": cost,
        "key_source": key,
        "created_at": at,
    }


TURNS = [TurnWindow(1, 100, 200, "completed"), TurnWindow(2, 300, None, "running")]


def test_a_session_is_totalled_and_each_call_goes_to_the_turn_that_started_last() -> None:
    usage = summarize_session_usage(
        "s",
        [
            _event(50, prompt=10, out=1, cost=0.001),  # before the first turn
            _event(150, prompt=2000, cached=1500, out=200, cost=0.02),
            _event(201, prompt=500, out=50, cost=0.005),  # the title after turn 1 ended
            _event(350, prompt=3000, cached=2500, out=300, cost=0.03),  # the running turn
        ],
        TURNS,
    )

    assert usage.totals.calls == 4 and usage.totals.prompt_tokens == 5510
    assert usage.totals.total_tokens == 5510 + 551 and usage.totals.cost_usd == 0.056
    first, second = usage.turns
    assert (first.calls, first.prompt_tokens, first.cost_usd) == (2, 2500, 0.025)
    assert first.cache_read_tokens == 1500 and first.status == "completed"
    assert (second.calls, second.cost_usd, second.ended_at) == (1, 0.03, None)
    assert usage.before_first_turn.calls == 1 and usage.before_first_turn.cost_usd == 0.001


def test_models_are_listed_dearest_first_and_own_key_spend_is_shown_apart() -> None:
    usage = summarize_session_usage(
        "s",
        [
            _event(150, model="cheap/m", cost=0.01),
            _event(160, model="dear/m", cost=0.5),
            _event(170, model="dear/m", cost=0.25, key="user"),
        ],
        TURNS,
    )

    assert [m.model for m in usage.models] == ["dear/m", "cheap/m"]
    assert usage.models[0].calls == 2 and usage.models[0].cost_usd == 0.75
    assert usage.totals.own_key_cost_usd == 0.25 and usage.totals.cost_usd == 0.76


def test_without_turn_traces_only_the_totals_are_returned() -> None:
    usage = summarize_session_usage("s", [_event(10), _event(20)], [])

    assert usage.turns == [] and usage.totals.calls == 2 and usage.before_first_turn.calls == 2


def test_an_empty_session_costs_nothing() -> None:
    usage = summarize_session_usage("s", [], TURNS)

    assert usage.totals.calls == 0 and usage.totals.cost_usd == 0.0
    assert [t.calls for t in usage.turns] == [0, 0]


# -- the endpoint -----------------------------------------------------------------------------

OWNER = {"X-Tenant-Org-Id": "usage_org", "X-Tenant-User-Id": "owner", "X-User-Role": "developer"}
OTHER_MEMBER = {**OWNER, "X-Tenant-User-Id": "someone_else"}
OTHER_ORG = {**OWNER, "X-Tenant-Org-Id": "elsewhere_org", "X-Tenant-User-Id": "owner"}


@pytest.fixture
def client() -> Any:
    with TestClient(app) as c:
        app.state.db_manager = None
        app.state.usage_ledger = UsageLedger(None)
        yield c


async def _record(ledger: UsageLedger, session_id: str, org: str = "usage_org") -> None:
    for at, cost in ((1000, 0.02), (1010, 0.03)):
        await ledger.record(
            UsageEvent(org, "owner", "m/a", 4000, 250, cost, session_id, 3000, 0, "platform", at)
        )


async def test_the_owner_sees_the_sessions_usage_and_nobody_else_does(client: TestClient) -> None:
    created = client.post("/v1/sessions", json={"title": "Costly"}, headers=OWNER)
    session_id = created.json()["session_id"]
    await _record(app.state.usage_ledger, session_id)

    mine = client.get(f"/v1/sessions/{session_id}/usage", headers=OWNER)
    member = client.get(f"/v1/sessions/{session_id}/usage", headers=OTHER_MEMBER)
    foreign = client.get(f"/v1/sessions/{session_id}/usage", headers=OTHER_ORG)

    assert mine.status_code == 200
    body = mine.json()
    assert body["totals"]["calls"] == 2 and body["totals"]["cost_usd"] == 0.05
    assert body["totals"]["cache_read_tokens"] == 6000 and body["totals"]["total_tokens"] == 8500
    assert body["models"][0]["model"] == "m/a" and body["turns"] == [], (
        "no turn traces without PostgreSQL"
    )
    assert member.status_code in (403, 404) and foreign.status_code in (403, 404)
    assert "cost_usd" not in member.text


def test_an_unknown_session_is_not_found(client: TestClient) -> None:
    assert client.get("/v1/sessions/session_nope/usage", headers=OWNER).status_code == 404


async def test_another_organizations_calls_are_never_counted(client: TestClient) -> None:
    created = client.post("/v1/sessions", json={"title": "Mine"}, headers=OWNER)
    session_id = created.json()["session_id"]
    await _record(app.state.usage_ledger, session_id, org="elsewhere_org")

    body = client.get(f"/v1/sessions/{session_id}/usage", headers=OWNER).json()

    assert body["totals"]["calls"] == 0


def test_a_finished_turn_carries_its_metrics_and_a_running_one_none() -> None:
    metrics = {"steps": 12, "model_ms": 60_000, "tool_ms": 9_000}
    usage = summarize_session_usage(
        "s",
        [_event(150)],
        [TurnWindow(1, 100, 200, "completed", metrics), TurnWindow(2, 300, None, "running")],
    )

    finished, running = usage.turns
    assert finished.metrics is not None and finished.metrics.steps == 12
    assert finished.metrics.model_ms == 60_000 and finished.metrics.compactions == 0
    assert running.metrics is None
