"""OpenTelemetry tracing: turn -> tool -> sandbox spans, exported over OTLP.

Tracing is opt-in. Without ``OTEL_EXPORTER_OTLP_ENDPOINT`` the API/SDK default to a no-op
tracer, so spans created by ``agent_core`` and ``sandbox_driver`` cost nothing and no
exporter thread is started. Everything else (headers, protocol, sampler, resource
attributes) is configured with the standard ``OTEL_*`` variables.
"""

from __future__ import annotations

import functools
import inspect
import logging
import os
from collections.abc import Awaitable, Callable

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

from api.logging_config import log_context

logger = logging.getLogger(__name__)

_tracer = trace.get_tracer("api.turn")

# Probes and scrapes would otherwise dominate the trace volume.
_UNTRACED_URLS = "health,ready,metrics,drain"


def tracing_enabled() -> bool:
    return bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip())


def setup_tracing(app: FastAPI) -> bool:
    """Install the OTLP exporter and instrument HTTP/WebSocket requests when configured."""
    if not tracing_enabled():
        return False
    resource = Resource.create({SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", "rocket-chat-api")})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls=_UNTRACED_URLS)
    logger.info("OpenTelemetry tracing enabled")
    return True


def traced_turn[**P, R](
    function: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    """Wrap a turn runner in an ``agent.turn`` span and bind its session id to the logs.

    The session id is read from the wrapped function's own signature so the decorator
    needs no knowledge of how callers pass it.
    """
    signature = inspect.signature(function)

    @functools.wraps(function)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        bound = signature.bind_partial(*args, **kwargs)
        session_id = str(bound.arguments.get("session_id") or "")
        with (
            log_context(session_id=session_id),
            _tracer.start_as_current_span(
                "agent.turn", attributes={"rocket.session_id": session_id}
            ) as span,
        ):
            try:
                return await function(*args, **kwargs)
            except BaseException as err:
                span.record_exception(err)
                span.set_status(Status(StatusCode.ERROR, str(err)))
                raise

    return wrapper
