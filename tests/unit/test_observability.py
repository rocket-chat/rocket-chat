"""Structured logs with tenant/session ids, spans across turn -> tool -> sandbox and auth-failure telemetry."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from agent_core import dispatcher as dispatcher_module
from agent_core.dispatcher import ToolDispatcher
from api import telemetry
from api.logging_config import CorrelationFilter, JsonFormatter, bind_log_context, log_context
from api.middleware.auth import AuthMiddleware
from api.services.metrics import AUTH_FAILURES, render_prometheus
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sandbox_driver import tracing as sandbox_tracing
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import WorkspaceSpec


def _format(record_message: str, **extra: Any) -> dict[str, Any]:
    record = logging.LogRecord("api.test", logging.INFO, __file__, 1, record_message, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    CorrelationFilter().filter(record)
    return json.loads(JsonFormatter().format(record))


def test_json_logs_stay_valid_for_messages_with_quotes_and_newlines() -> None:
    payload = _format('said "hi"\nthen left')
    assert payload["message"] == 'said "hi"\nthen left'
    assert payload["level"] == "INFO"


def test_log_lines_carry_the_bound_tenant_and_session() -> None:
    with log_context(org_id="org_a", session_id="s1"):
        inside = _format("inside")
    outside = _format("outside")
    assert inside["org_id"] == "org_a"
    assert inside["session_id"] == "s1"
    assert "org_id" not in outside


def test_bound_context_does_not_leak_between_tasks() -> None:
    async def worker(name: str) -> dict[str, Any]:
        bind_log_context(session_id=name)
        await asyncio.sleep(0)
        return _format("x")

    async def run() -> list[dict[str, Any]]:
        return list(await asyncio.gather(worker("a"), worker("b")))

    first, second = asyncio.run(run())
    assert (first["session_id"], second["session_id"]) == ("a", "b")


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "_tracer", provider.get_tracer("test"))
    monkeypatch.setattr(dispatcher_module, "_tracer", provider.get_tracer("test"))
    monkeypatch.setattr(sandbox_tracing, "_tracer", provider.get_tracer("test"))
    yield exporter


class _StubRegistry:
    async def call_tool(self, name: str, args: dict[str, Any]) -> str:
        if name == "boom":
            raise RuntimeError("tool exploded")
        return "ok"


def test_a_turn_parents_its_tool_and_sandbox_spans(spans: InMemorySpanExporter) -> None:
    driver = FakeSandboxDriver()

    class Registry:
        async def call_tool(self, name: str, args: dict[str, Any]) -> str:
            await driver.ensure_workspace(
                WorkspaceSpec(
                    session_id="s1", tenant_org_id="o", tenant_user_id="u", container_image="x"
                )
            )
            await driver.start_sandbox("s1")
            result = await driver.exec_command("s1", "echo hi")
            return result.stdout

    @telemetry.traced_turn
    async def run_turn(session_id: str) -> None:
        queue: asyncio.Queue[Any] = asyncio.Queue()
        await ToolDispatcher(Registry()).execute_single_tool(  # type: ignore[arg-type]
            session_id, {"id": "c1", "name": "bash", "arguments": '{"secret": "hunter2"}'}, queue
        )

    asyncio.run(run_turn("s1"))

    by_name = {span.name: span for span in spans.get_finished_spans()}
    assert set(by_name) >= {"agent.turn", "agent.tool", "sandbox.exec"}
    turn, tool, exec_span = by_name["agent.turn"], by_name["agent.tool"], by_name["sandbox.exec"]
    assert tool.parent is not None and tool.parent.span_id == turn.context.span_id
    assert exec_span.parent is not None and exec_span.parent.span_id == tool.context.span_id
    assert turn.attributes is not None and turn.attributes["rocket.session_id"] == "s1"
    assert exec_span.attributes is not None and exec_span.attributes["rocket.exit_code"] == 0
    assert "hunter2" not in json.dumps({s.name: dict(s.attributes or {}) for s in by_name.values()})


def test_a_failing_tool_marks_its_span_as_an_error(spans: InMemorySpanExporter) -> None:
    async def run() -> None:
        queue: asyncio.Queue[Any] = asyncio.Queue()
        await ToolDispatcher(_StubRegistry()).execute_single_tool(  # type: ignore[arg-type]
            "s1", {"id": "c1", "name": "boom", "arguments": "{}"}, queue
        )

    asyncio.run(run())
    (tool,) = [s for s in spans.get_finished_spans() if s.name == "agent.tool"]
    assert tool.status.status_code.name == "ERROR"
    assert tool.attributes is not None and tool.attributes["rocket.tool_status"] == "error"


def test_tracing_stays_off_without_an_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert telemetry.setup_tracing(FastAPI()) is False


def _guarded_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/v1/private")
    async def private() -> dict[str, str]:
        return {"ok": "yes"}

    return app


def test_rejected_requests_are_counted_and_logged_without_the_credential(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEV_AUTH_BYPASS", raising=False)
    monkeypatch.delenv("ROCKET_TRUST_PROXY_HEADERS", raising=False)
    client = TestClient(_guarded_app())
    before_missing = AUTH_FAILURES.value({"reason": "missing_credentials"})
    before_invalid = AUTH_FAILURES.value({"reason": "invalid_credentials"})
    with caplog.at_level(logging.WARNING):
        assert client.get("/v1/private").status_code == 401
        assert (
            client.get("/v1/private", headers={"Authorization": "Bearer s3cret-token"}).status_code
            == 401
        )

    assert AUTH_FAILURES.value({"reason": "missing_credentials"}) == before_missing + 1
    assert AUTH_FAILURES.value({"reason": "invalid_credentials"}) == before_invalid + 1
    assert "s3cret-token" not in caplog.text
    assert 'rocket_auth_failures_total{reason="invalid_credentials"}' in render_prometheus()


def test_every_response_carries_a_request_id_and_keeps_a_well_formed_inbound_one() -> None:
    from api.main import app

    client = TestClient(app)
    generated = client.get("/health").headers["X-Request-ID"]
    assert len(generated) == 32
    assert (
        client.get("/health", headers={"X-Request-ID": "gateway-abc-123"}).headers["X-Request-ID"]
        == "gateway-abc-123"
    )
    hostile = client.get("/health", headers={"X-Request-ID": "bad id\twith spaces"})
    assert hostile.headers["X-Request-ID"] != "bad id\twith spaces"
