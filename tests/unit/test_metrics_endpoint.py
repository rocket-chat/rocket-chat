"""Unit tests for the dependency-free Prometheus exposition (M9.2, G3, G7)."""

from __future__ import annotations

import asyncio

import pytest
from api.services import metrics as metrics_module
from api.services.metrics import (
    METRICS,
    GlobalConcurrencyGate,
    MetricsRegistry,
    SessionWriteLocks,
    build_backpressure,
    record_tool_call,
    record_turn,
    websocket_connection_closed,
    websocket_connection_opened,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

DOCUMENTED_SERIES = (
    "rocket_turns_total",
    "rocket_turn_duration_seconds",
    "rocket_turn_time_to_first_token_seconds",
    "rocket_tool_calls_total",
    "rocket_tool_duration_seconds",
    "rocket_model_tokens_total",
    "rocket_model_cost_usd_total",
    "rocket_websocket_connections",
    "rocket_code_index_sessions",
    "rocket_code_index_bytes",
    "rocket_code_index_hits_total",
    "rocket_code_index_misses_total",
    "rocket_code_index_evictions_total",
    "rocket_code_index_refusals_total",
    "rocket_quota_rejections_total",
    "rocket_prompt_cache_hit_ratio",
)


class _FakeCodeIndexCache:
    def stats(self) -> dict[str, int]:
        return {
            "sessions": 3,
            "bytes": 3072,
            "max_sessions": 16,
            "max_bytes": 201326592,
            "hits": 11,
            "misses": 4,
            "evictions": 2,
            "refusals": 1,
        }


class _FakeQuotaGate:
    def counters(self) -> dict[str, int]:
        return {"quota_denied": 7, "turns_admitted": 21}


class _FakeSlackAssistant:
    quota_gate = _FakeQuotaGate()


class _FakeGateway:
    def __init__(self) -> None:
        self.usage_ledger: list[dict[str, object]] = [
            {
                "model": "openrouter/deepseek/deepseek-v4.1-flash",
                "prompt_tokens": 1000,
                "completion_tokens": 200,
                "cache_read_tokens": 650,
                "cache_creation_tokens": 100,
                "cost_usd": 0.0123,
            }
        ]


class _FakeBotManager:
    """Two running bots: the endpoint reports their counters summed."""

    def all(self) -> list[_FakeSlackAssistant]:
        return [_FakeSlackAssistant(), _FakeSlackAssistant()]


def _build_metrics_app() -> FastAPI:
    app = FastAPI()
    app.add_api_route("/metrics", metrics_module.metrics_endpoint, methods=["GET"])
    app.state.code_index_cache = _FakeCodeIndexCache()
    app.state.slack_bot_manager = _FakeBotManager()
    app.state.llm_gateway = _FakeGateway()
    gate, locks = build_backpressure(limit=5)
    app.state.global_concurrency_gate = gate
    app.state.session_write_locks = locks
    return app


def test_metrics_endpoint_exposes_documented_series() -> None:
    app = _build_metrics_app()
    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    for series in DOCUMENTED_SERIES:
        assert series in body, f"missing documented series {series}"


def test_metrics_reflects_code_index_and_quota_state() -> None:
    app = _build_metrics_app()
    with TestClient(app) as client:
        body = client.get("/metrics").text
    assert "rocket_code_index_sessions 3.0" in body
    assert "rocket_code_index_bytes 3072.0" in body
    assert "rocket_code_index_hits_total 11.0" in body
    assert "rocket_code_index_refusals_total 1.0" in body
    assert 'rocket_quota_rejections_total{scope="slack"} 14.0' in body


def test_metrics_computes_prompt_cache_hit_ratio_from_ledger() -> None:
    app = _build_metrics_app()
    with TestClient(app) as client:
        body = client.get("/metrics").text
    assert "rocket_prompt_cache_hit_ratio 0.65" in body
    assert (
        'rocket_model_tokens_total{model="openrouter/deepseek/deepseek-v4.1-flash",type="prompt"} 1000.0'
        in body
    )
    assert (
        'rocket_model_tokens_total{model="openrouter/deepseek/deepseek-v4.1-flash",type="cache_read"} 650.0'
        in body
    )
    assert (
        'rocket_model_cost_usd_total{model="openrouter/deepseek/deepseek-v4.1-flash"} 0.0123'
        in body
    )


def test_metrics_reports_concurrency_limit_and_queue_depth() -> None:
    app = _build_metrics_app()
    # No turn is running yet, so queue and active are zero but the ceiling is known.
    with TestClient(app) as client:
        body = client.get("/metrics").text
    assert "rocket_turn_concurrency_limit 5.0" in body
    assert "rocket_turn_queue_depth 0.0" in body
    assert "rocket_turn_active 0.0" in body


def test_histogram_buckets_are_cumulative() -> None:
    registry = MetricsRegistry()
    histogram = registry.histogram("demo_seconds", "demo", (0.1, 1.0, 10.0))
    histogram.observe(None, 0.05)
    histogram.observe(None, 0.5)
    histogram.observe(None, 5.0)
    rendered = "\n".join(histogram.render())
    assert 'demo_seconds_bucket{le="0.1"} 1' in rendered
    assert 'demo_seconds_bucket{le="1.0"} 2' in rendered
    assert 'demo_seconds_bucket{le="10.0"} 3' in rendered
    assert 'demo_seconds_bucket{le="+Inf"} 3' in rendered
    assert "demo_seconds_count 3" in rendered
    assert "demo_seconds_sum 5.55" in rendered


def test_series_cardinality_is_bounded() -> None:
    registry = MetricsRegistry(max_series=3)
    counter = registry.counter("demo_total", "demo", ("tool",))
    for index in range(10):
        counter.inc({"tool": f"tool_{index}"})
    assert len(counter._values) == 3
    assert counter.dropped == 7
    # The drop is observable through the registry rather than silent.
    assert registry.dropped_series() == {"demo_total": 7}
    rendered = "\n".join(counter.render())
    assert "demo_total" in rendered


@pytest.mark.asyncio
async def test_global_concurrency_gate_bounds_and_reports_depth() -> None:
    gate = GlobalConcurrencyGate(limit=1)
    entered: list[str] = []

    async def worker(name: str) -> None:
        async with gate.slot():
            entered.append(name)
            await asyncio.sleep(0.01)

    first = asyncio.create_task(worker("a"))
    await asyncio.sleep(0)  # let `a` acquire
    assert gate.active == 1
    second = asyncio.create_task(worker("b"))
    await asyncio.sleep(0)
    assert gate.queued == 1
    await asyncio.gather(first, second)
    assert gate.active == 0
    assert gate.queued == 0
    assert entered == ["a", "b"]


@pytest.mark.asyncio
async def test_session_write_locks_serialize_one_writer() -> None:
    locks = SessionWriteLocks(capacity=2)
    order: list[str] = []

    async def writer(name: str) -> None:
        async with locks.lock("session-1"):
            order.append(f"{name}-enter")
            await asyncio.sleep(0.01)
            order.append(f"{name}-exit")

    await asyncio.gather(writer("a"), writer("b"))
    # The two critical sections never interleave.
    assert order[0].endswith("-enter")
    assert order[1].endswith("-exit")
    assert order[2].endswith("-enter")
    assert order[3].endswith("-exit")


@pytest.mark.asyncio
async def test_session_write_locks_evict_idle_entries() -> None:
    locks = SessionWriteLocks(capacity=2)
    async with locks.lock("s1"):
        pass
    async with locks.lock("s2"):
        pass
    assert locks.size == 2
    async with locks.lock("s3"):
        pass
    assert locks.size == 2


def test_recording_helpers_update_the_global_registry() -> None:
    before_turns = METRICS.counter("rocket_turns_total", "", ("kind",))._values[("conversational",)]
    record_turn("conversational", 0.42, ttft_seconds=0.12)
    after_turns = METRICS.counter("rocket_turns_total", "", ("kind",))._values[("conversational",)]
    assert after_turns == before_turns + 1
    record_tool_call("file_read", duration_seconds=0.03)
    record_tool_call("file_read")
    assert METRICS.counter("rocket_tool_calls_total", "", ("tool",))._values[("file_read",)] >= 2


def test_websocket_connection_gauge_moves() -> None:
    gauge = METRICS.gauge("rocket_websocket_connections", "")
    gauge.set(None, 0.0)
    websocket_connection_opened()
    before = gauge._values[()]
    websocket_connection_closed()
    assert gauge._values[()] == before - 1


def test_operational_routes_are_attached_to_the_real_app() -> None:
    """The S11 endpoints must be reachable on the application object itself."""
    from api.main import app

    client = TestClient(app)
    try:
        assert client.get("/metrics").status_code == 200
        assert client.get("/v1/ready").status_code in (200, 503)
        assert client.post("/v1/drain").status_code == 200
    finally:
        app.state.draining = False
