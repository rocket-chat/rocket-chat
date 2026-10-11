"""Dependency-free Prometheus metrics, readiness probing and turn backpressure.

This module is the operational surface of the control plane:

* a bounded, dependency-free Prometheus text exposition. No ``prometheus_client``
  and no OpenTelemetry SDK are pulled in: the exposition format is small enough to
  emit directly, and a real OTel tracing hook would add a heavyweight dependency
  (see the session report, section 4);
* ``/v1/ready`` pings PostgreSQL (async, with a hard timeout) and reports the
  bounded code-index cache stats, returning **non-200 when the database is
  unreachable**;
* ``/metrics`` renders the documented series;
* a process-wide turn concurrency gate with a queue-depth gauge and a bounded
  per-session single-writer lock registry.

Everything here is deliberately in-process: the event bus, approval gates, code
index and active turns already are (risk R1), so ``replicas: 1`` plus session
affinity remains the shipped topology.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sandbox_driver.blocking import blocking_calls_in_flight
from sqlalchemy import text

from api.services.shutdown import begin_shutdown

logger = logging.getLogger(__name__)

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

# Turn labels are a closed set (TTFT is split by conversational vs sandbox),
# so the label values are fixed and cannot inflate cardinality.
TURN_KINDS: tuple[str, ...] = ("conversational", "sandbox")

# Latency buckets in seconds. The TTFT targets are <2 s conversational / <6 s
# sandbox p95, so the boundaries straddle both thresholds.
_TURN_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 6.0, 10.0, 30.0, 60.0)

# A pathological caller could otherwise mint an unbounded number of label
# combinations (one per tool name / model id). Each family keeps at most this many
# series and counts the drops, so the metrics endpoint can never be the cause of an
# OOMKill.
DEFAULT_MAX_SERIES_PER_METRIC = 200

# Bound the number of distinct models surfaced from the usage ledger; the tail is
# folded into a single ``other`` series rather than allowed to grow without limit.
MAX_MODEL_SERIES = 50

DEFAULT_DB_PING_TIMEOUT_SECONDS = 2.0
DEFAULT_GLOBAL_CONCURRENCY_LIMIT = 16
DEFAULT_SESSION_LOCK_CAPACITY = 256


def global_concurrency_limit() -> int:
    """Maximum concurrent turns in this process (``ROCKET_GLOBAL_CONCURRENCY_LIMIT``)."""
    raw = os.getenv("ROCKET_GLOBAL_CONCURRENCY_LIMIT", "").strip()
    if raw.isdigit() and int(raw) >= 1:
        return int(raw)
    return DEFAULT_GLOBAL_CONCURRENCY_LIMIT


def session_lock_capacity() -> int:
    """Maximum distinct session locks retained before idle ones are evicted."""
    raw = os.getenv("ROCKET_SESSION_LOCK_CAPACITY", "").strip()
    if raw.isdigit() and int(raw) >= 1:
        return int(raw)
    return DEFAULT_SESSION_LOCK_CAPACITY


def db_ping_timeout_seconds() -> float:
    """Deadline for the readiness database ping (``ROCKET_DB_PING_TIMEOUT_SECONDS``)."""
    raw = os.getenv("ROCKET_DB_PING_TIMEOUT_SECONDS", "").strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_DB_PING_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_DB_PING_TIMEOUT_SECONDS


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_labels(label_names: tuple[str, ...], values: tuple[str, ...]) -> str:
    if not label_names:
        return ""
    pairs = ",".join(
        f'{name}="{_escape_label(value)}"' for name, value in zip(label_names, values, strict=True)
    )
    return "{" + pairs + "}"


class _Family:
    """Common label bookkeeping for a single Prometheus metric family."""

    metric_type = "untyped"

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: tuple[str, ...] = (),
        max_series: int = DEFAULT_MAX_SERIES_PER_METRIC,
    ) -> None:
        self.name = name
        self.help_text = help_text
        self.label_names = label_names
        self.max_series = max_series
        self.dropped = 0

    def header(self) -> list[str]:
        return [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} {self.metric_type}"]

    def _key(self, labels: Mapping[str, str] | None) -> tuple[str, ...]:
        if not self.label_names:
            return ()
        source = labels or {}
        return tuple(str(source.get(name, "")) for name in self.label_names)

    def _admit(self, key: tuple[str, ...], known: Mapping[tuple[str, ...], object]) -> bool:
        """Whether a (possibly new) label combination may be retained."""
        if key in known:
            return True
        if len(known) >= self.max_series:
            self.dropped += 1
            return False
        return True

    def render(self) -> list[str]:
        raise NotImplementedError


class CounterFamily(_Family):
    """A monotonically increasing counter family."""

    metric_type = "counter"

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: tuple[str, ...] = (),
        max_series: int = DEFAULT_MAX_SERIES_PER_METRIC,
    ) -> None:
        super().__init__(name, help_text, label_names, max_series)
        self._values: dict[tuple[str, ...], float] = {}

    def inc(self, labels: Mapping[str, str] | None = None, amount: float = 1.0) -> None:
        key = self._key(labels)
        if not self._admit(key, self._values):
            return
        self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, labels: Mapping[str, str] | None = None) -> float:
        return self._values.get(self._key(labels), 0.0)

    def set(self, labels: Mapping[str, str] | None, value: float) -> None:
        """Set a counter's absolute value (mirrors an externally-held counter)."""
        key = self._key(labels)
        if not self._admit(key, self._values):
            return
        self._values[key] = float(value)

    def render(self) -> list[str]:
        lines = self.header()
        for key in sorted(self._values):
            lines.append(f"{self.name}{_format_labels(self.label_names, key)} {self._values[key]}")
        return lines


class GaugeFamily(_Family):
    """A gauge family that can be set, incremented or decremented."""

    metric_type = "gauge"

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: tuple[str, ...] = (),
        max_series: int = DEFAULT_MAX_SERIES_PER_METRIC,
    ) -> None:
        super().__init__(name, help_text, label_names, max_series)
        self._values: dict[tuple[str, ...], float] = {}

    def set(self, labels: Mapping[str, str] | None, value: float) -> None:
        key = self._key(labels)
        if not self._admit(key, self._values):
            return
        self._values[key] = float(value)

    def inc(self, labels: Mapping[str, str] | None = None, amount: float = 1.0) -> None:
        key = self._key(labels)
        if not self._admit(key, self._values):
            return
        self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, labels: Mapping[str, str] | None = None, amount: float = 1.0) -> None:
        self.inc(labels, -amount)

    def render(self) -> list[str]:
        lines = self.header()
        for key in sorted(self._values):
            lines.append(f"{self.name}{_format_labels(self.label_names, key)} {self._values[key]}")
        return lines


class _HistogramState:
    """Per-series histogram counts. Buckets are stored cumulatively."""

    __slots__ = ("buckets", "count", "total")

    def __init__(self, bucket_count: int) -> None:
        self.buckets = [0] * bucket_count
        self.total = 0.0
        self.count = 0


class HistogramFamily(_Family):
    """A cumulative histogram family with fixed bucket boundaries."""

    metric_type = "histogram"

    def __init__(
        self,
        name: str,
        help_text: str,
        buckets: tuple[float, ...],
        label_names: tuple[str, ...] = (),
        max_series: int = DEFAULT_MAX_SERIES_PER_METRIC,
    ) -> None:
        super().__init__(name, help_text, label_names, max_series)
        self.buckets = tuple(sorted(buckets))
        self._values: dict[tuple[str, ...], _HistogramState] = {}

    def ensure(self, labels: Mapping[str, str] | None = None) -> None:
        """Create a zeroed series so an unused-but-documented label is visible."""
        key = self._key(labels)
        if self._admit(key, self._values):
            self._values.setdefault(key, _HistogramState(len(self.buckets)))

    def observe(self, labels: Mapping[str, str] | None, value: float) -> None:
        key = self._key(labels)
        if not self._admit(key, self._values):
            return
        state = self._values.setdefault(key, _HistogramState(len(self.buckets)))
        # Increment every boundary at or above the observation, which makes each
        # bucket inherently cumulative (no accumulation needed at render time).
        for index, boundary in enumerate(self.buckets):
            if value <= boundary:
                state.buckets[index] += 1
        state.total += float(value)
        state.count += 1

    def render(self) -> list[str]:
        lines = self.header()
        for key in sorted(self._values):
            state = self._values[key]
            for boundary, count in zip(self.buckets, state.buckets, strict=True):
                bucket_labels = _format_labels((*self.label_names, "le"), (*key, str(boundary)))
                lines.append(f"{self.name}_bucket{bucket_labels} {count}")
            inf_labels = _format_labels((*self.label_names, "le"), (*key, "+Inf"))
            lines.append(f"{self.name}_bucket{inf_labels} {state.count}")
            base_labels = _format_labels(self.label_names, key)
            lines.append(f"{self.name}_sum{base_labels} {state.total}")
            lines.append(f"{self.name}_count{base_labels} {state.count}")
        return lines


class MetricsRegistry:
    """A bounded registry of counters, gauges and histograms."""

    def __init__(self, max_series: int = DEFAULT_MAX_SERIES_PER_METRIC) -> None:
        self.max_series = max_series
        self._counters: dict[str, CounterFamily] = {}
        self._gauges: dict[str, GaugeFamily] = {}
        self._histograms: dict[str, HistogramFamily] = {}

    def counter(
        self, name: str, help_text: str, label_names: tuple[str, ...] = ()
    ) -> CounterFamily:
        family = self._counters.get(name)
        if family is None:
            family = CounterFamily(name, help_text, label_names, self.max_series)
            self._counters[name] = family
        return family

    def gauge(self, name: str, help_text: str, label_names: tuple[str, ...] = ()) -> GaugeFamily:
        family = self._gauges.get(name)
        if family is None:
            family = GaugeFamily(name, help_text, label_names, self.max_series)
            self._gauges[name] = family
        return family

    def histogram(
        self,
        name: str,
        help_text: str,
        buckets: tuple[float, ...],
        label_names: tuple[str, ...] = (),
    ) -> HistogramFamily:
        family = self._histograms.get(name)
        if family is None:
            family = HistogramFamily(name, help_text, buckets, label_names, self.max_series)
            self._histograms[name] = family
        return family

    def families(self) -> list[_Family]:
        return [*self._counters.values(), *self._gauges.values(), *self._histograms.values()]

    def dropped_series(self) -> dict[str, int]:
        return {family.name: family.dropped for family in self.families() if family.dropped > 0}


# ---------------------------------------------------------------------------
# Documented metric families.
# ---------------------------------------------------------------------------

METRICS = MetricsRegistry()

TURNS_TOTAL = METRICS.counter(
    "rocket_turns_total",
    "Completed agent turns, split by conversational vs sandbox execution.",
    ("kind",),
)
TURN_DURATION = METRICS.histogram(
    "rocket_turn_duration_seconds",
    "End-to-end turn duration in seconds, split by turn kind.",
    _TURN_BUCKETS,
    ("kind",),
)
TTFT = METRICS.histogram(
    "rocket_turn_time_to_first_token_seconds",
    "Time from turn start to the first streamed token, split by turn kind.",
    _TURN_BUCKETS,
    ("kind",),
)
TOOL_CALLS = METRICS.counter(
    "rocket_tool_calls_total",
    "Tool invocations, labelled by tool name.",
    ("tool",),
)
TOOL_DURATION = METRICS.histogram(
    "rocket_tool_duration_seconds",
    "Tool execution duration in seconds, labelled by tool name.",
    (0.05, 0.1, 0.5, 1.0, 5.0, 15.0, 60.0, 300.0),
    ("tool",),
)
WEB_SOCKET_CONNECTIONS = METRICS.gauge(
    "rocket_websocket_connections",
    "Currently open cockpit WebSocket connections.",
)
QUOTA_REJECTIONS = METRICS.counter(
    "rocket_quota_rejections_total",
    "Requests rejected by a quota or rate-limit gate, labelled by scope.",
    ("scope",),
)
AUTH_FAILURES = METRICS.counter(
    "rocket_auth_failures_total",
    "Requests rejected for missing or invalid credentials, labelled by reason.",
    ("reason",),
)
PROMPT_CACHE_HIT_RATIO = METRICS.gauge(
    "rocket_prompt_cache_hit_ratio",
    "Cached prompt tokens divided by total prompt tokens from the usage ledger.",
)
TURN_QUEUE_DEPTH = METRICS.gauge(
    "rocket_turn_queue_depth",
    "Turns admitted but waiting for a global concurrency slot.",
)
TURN_ACTIVE = METRICS.gauge(
    "rocket_turn_active",
    "Turns currently holding a global concurrency slot.",
)
TURN_CONCURRENCY_LIMIT = METRICS.gauge(
    "rocket_turn_concurrency_limit",
    "Configured process-wide concurrent turn ceiling.",
)
DOCKER_CALLS_IN_FLIGHT = METRICS.gauge(
    "rocket_docker_blocking_calls",
    "Docker SDK calls running or queued on the driver's dedicated thread pool.",
)
SESSION_LOCKS = METRICS.gauge(
    "rocket_session_write_locks",
    "Per-session single-writer locks currently retained.",
)

# Pre-create the closed-set series so the documented metrics are visible before the
# first turn (Prometheus treats an absent family as "no data", not zero).
for _kind in TURN_KINDS:
    TURNS_TOTAL.inc({"kind": _kind}, 0.0)
    TURN_DURATION.ensure({"kind": _kind})
    TTFT.ensure({"kind": _kind})
QUOTA_REJECTIONS.set({"scope": "slack"}, 0.0)
for _reason in ("missing_credentials", "invalid_credentials"):
    AUTH_FAILURES.inc({"reason": _reason}, 0.0)
WEB_SOCKET_CONNECTIONS.set(None, 0.0)
PROMPT_CACHE_HIT_RATIO.set(None, 0.0)


# ---------------------------------------------------------------------------
# Recording helpers for the HTTP / orchestrator layer.
# ---------------------------------------------------------------------------


def record_turn(kind: str, duration_seconds: float, ttft_seconds: float | None = None) -> None:
    """Record a completed turn's duration and (when known) its time-to-first-token."""
    TURNS_TOTAL.inc({"kind": kind})
    TURN_DURATION.observe({"kind": kind}, duration_seconds)
    if ttft_seconds is not None:
        TTFT.observe({"kind": kind}, ttft_seconds)


def record_tool_call(tool: str, duration_seconds: float | None = None) -> None:
    """Record one tool invocation and, when measured, its duration."""
    TOOL_CALLS.inc({"tool": tool})
    if duration_seconds is not None:
        TOOL_DURATION.observe({"tool": tool}, duration_seconds)


def record_auth_failure(reason: str) -> None:
    AUTH_FAILURES.inc({"reason": reason})


def record_quota_rejection(scope: str = "slack") -> None:
    """Record a request rejected by a quota or rate-limit gate."""
    QUOTA_REJECTIONS.inc({"scope": scope})


def websocket_connection_opened() -> None:
    """Register an open cockpit WebSocket connection."""
    WEB_SOCKET_CONNECTIONS.inc()


def websocket_connection_closed() -> None:
    """Register a closed cockpit WebSocket connection."""
    WEB_SOCKET_CONNECTIONS.dec()


# ---------------------------------------------------------------------------
# App-state-derived families (code index, quota, model usage, concurrency).
# ---------------------------------------------------------------------------


def _code_index_stats(app_state: object | None) -> dict[str, int | float]:
    cache = getattr(app_state, "code_index_cache", None)
    stats = getattr(cache, "stats", None)
    if not callable(stats):
        return {}
    result = stats()
    return result if isinstance(result, dict) else {}


def _quota_counters(app_state: object | None) -> dict[str, int]:
    assistant = getattr(app_state, "slack_assistant", None)
    gate = getattr(assistant, "quota_gate", None)
    counters = getattr(gate, "counters", None)
    if not callable(counters):
        return {}
    result = counters()
    return result if isinstance(result, dict) else {}


def _ledger_entries(app_state: object | None) -> list[dict[str, object]]:
    """Read the LLM usage ledger defensively.

    The real gateway keeps it as ``_usage_ledger`` and the deterministic stub as
    ``usage_ledger``; neither package is owned by this session, so the metrics
    layer reads whichever exists and no-ops otherwise. A first-class
    ``get_usage_ledger()`` accessor is handed over in the session report.
    """
    gateway = getattr(app_state, "llm_gateway", None)
    for attribute in ("usage_ledger", "_usage_ledger"):
        ledger = getattr(gateway, attribute, None)
        if isinstance(ledger, list):
            return [entry for entry in ledger if isinstance(entry, dict)]
    return []


def _render_code_index(app_state: object | None) -> list[str]:
    stats = _code_index_stats(app_state)
    if not stats:
        return []
    return [
        "# HELP rocket_code_index_sessions Cached per-session code indexes.",
        "# TYPE rocket_code_index_sessions gauge",
        f"rocket_code_index_sessions {float(stats.get('sessions', 0))}",
        "# HELP rocket_code_index_bytes Resident bytes held by the code-index cache.",
        "# TYPE rocket_code_index_bytes gauge",
        f"rocket_code_index_bytes {float(stats.get('bytes', 0))}",
        "# HELP rocket_code_index_hits_total Code-index cache hits.",
        "# TYPE rocket_code_index_hits_total counter",
        f"rocket_code_index_hits_total {float(stats.get('hits', 0))}",
        "# HELP rocket_code_index_misses_total Code-index cache misses.",
        "# TYPE rocket_code_index_misses_total counter",
        f"rocket_code_index_misses_total {float(stats.get('misses', 0))}",
        "# HELP rocket_code_index_evictions_total Code-index evictions (LRU/TTL/byte budget).",
        "# TYPE rocket_code_index_evictions_total counter",
        f"rocket_code_index_evictions_total {float(stats.get('evictions', 0))}",
        "# HELP rocket_code_index_refusals_total Indexes refused for exceeding the byte budget.",
        "# TYPE rocket_code_index_refusals_total counter",
        f"rocket_code_index_refusals_total {float(stats.get('refusals', 0))}",
    ]


def _render_quota(app_state: object | None) -> list[str]:
    counters = _quota_counters(app_state)
    if not counters:
        return []
    denied = int(counters.get("quota_denied", 0))
    admitted = int(counters.get("turns_admitted", 0))
    # The quota service holds the authoritative Slack denial count; mirror it onto
    # the documented family so Grafana does not have to know the internal name.
    QUOTA_REJECTIONS.set({"scope": "slack"}, float(denied))
    return [
        "# HELP rocket_slack_turns_admitted_total Slack turns admitted by the workspace quota gate.",
        "# TYPE rocket_slack_turns_admitted_total counter",
        f"rocket_slack_turns_admitted_total {admitted}",
    ]


def _render_model_usage(app_state: object | None) -> list[str]:
    entries = _ledger_entries(app_state)
    if not entries:
        return []
    per_model_tokens: dict[tuple[str, str], float] = {}
    per_model_cost: dict[str, float] = {}
    prompt_total = 0
    cache_read_total = 0

    def _as_int(entry: dict[str, object], key: str) -> int:
        value = entry.get(key, 0)
        return int(value) if isinstance(value, (int, float)) else 0

    for entry in entries:
        model = str(entry.get("model", "unknown"))
        if model not in per_model_cost and len(per_model_cost) >= MAX_MODEL_SERIES:
            model = "other"
        prompt = _as_int(entry, "prompt_tokens")
        completion = _as_int(entry, "completion_tokens")
        cache_read = _as_int(entry, "cache_read_tokens")
        cache_creation = _as_int(entry, "cache_creation_tokens")
        cost = entry.get("cost_usd", 0.0)
        prompt_total += prompt
        cache_read_total += cache_read
        for token_type, amount in (
            ("prompt", prompt),
            ("completion", completion),
            ("cache_read", cache_read),
            ("cache_creation", cache_creation),
        ):
            per_model_tokens[(model, token_type)] = (
                per_model_tokens.get((model, token_type), 0.0) + amount
            )
        per_model_cost[model] = per_model_cost.get(model, 0.0) + (
            float(cost) if isinstance(cost, (int, float)) else 0.0
        )

    lines = [
        "# HELP rocket_model_tokens_total Model tokens by model and type.",
        "# TYPE rocket_model_tokens_total counter",
    ]
    for model, token_type in sorted(per_model_tokens):
        lines.append(
            f'rocket_model_tokens_total{{model="{_escape_label(model)}",'
            f'type="{token_type}"}} {per_model_tokens[(model, token_type)]}'
        )
    lines += [
        "# HELP rocket_model_cost_usd_total Estimated model cost in USD by model.",
        "# TYPE rocket_model_cost_usd_total counter",
    ]
    for model in sorted(per_model_cost):
        lines.append(
            f'rocket_model_cost_usd_total{{model="{_escape_label(model)}"}} {per_model_cost[model]}'
        )
    PROMPT_CACHE_HIT_RATIO.set(None, (cache_read_total / prompt_total) if prompt_total > 0 else 0.0)
    return lines


def _render_concurrency(app_state: object | None) -> list[str]:
    gate = getattr(app_state, "global_concurrency_gate", None)
    TURN_ACTIVE.set(None, float(getattr(gate, "active", 0)) if gate is not None else 0.0)
    TURN_QUEUE_DEPTH.set(None, float(getattr(gate, "queued", 0)) if gate is not None else 0.0)
    TURN_CONCURRENCY_LIMIT.set(None, float(getattr(gate, "limit", 0)) if gate is not None else 0.0)
    DOCKER_CALLS_IN_FLIGHT.set(None, float(blocking_calls_in_flight()))
    locks = getattr(app_state, "session_write_locks", None)
    SESSION_LOCKS.set(None, float(getattr(locks, "size", 0)) if locks is not None else 0.0)
    return []


def render_prometheus(app_state: object | None = None) -> str:
    """Render every metric family in the Prometheus text exposition format."""
    # Refresh app-state-derived counters/gauges BEFORE the family render so the
    # registered families below expose this scrape's values, not the previous one.
    extra: list[str] = []
    extra.extend(_render_quota(app_state))
    extra.extend(_render_model_usage(app_state))
    _render_concurrency(app_state)
    extra.extend(_render_code_index(app_state))

    lines: list[str] = []
    for family in METRICS.families():
        lines.extend(family.render())
    dropped = METRICS.dropped_series()
    if dropped:
        lines += [
            "# HELP rocket_metrics_series_dropped_total Label combinations dropped to bound cardinality.",
            "# TYPE rocket_metrics_series_dropped_total counter",
        ]
        for metric in sorted(dropped):
            lines.append(
                f'rocket_metrics_series_dropped_total{{metric="{_escape_label(metric)}"}} '
                f"{dropped[metric]}"
            )
    lines.extend(extra)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# HTTP endpoints (attached in main.py; see the session report for exact lines).
# ---------------------------------------------------------------------------


async def metrics_endpoint(request: Request) -> Response:
    """Prometheus scrape endpoint for the documented series."""
    return Response(
        content=render_prometheus(getattr(request.app, "state", None)),
        media_type=PROMETHEUS_CONTENT_TYPE,
    )


async def _ping_database(db_manager: object) -> tuple[bool, str]:
    """Ping PostgreSQL with a hard timeout; returns ``(ok, detail)``."""
    session_factory = getattr(db_manager, "session_factory", None)
    session_cm = getattr(db_manager, "session", None)
    if session_factory is None or not callable(session_cm):
        # A manager constructed without a URL is the in-memory dev mode, which is
        # healthy; a manager that has a URL but no engine is un-initialised.
        if getattr(db_manager, "db_url", None):
            return False, "uninitialized"
        return True, "not_configured"
    try:
        async with asyncio.timeout(db_ping_timeout_seconds()):
            async with session_cm() as session:
                await session.execute(text("SELECT 1"))
        return True, "connected"
    except Exception as err:
        logger.warning("Readiness database ping failed: %s", err)
        return False, type(err).__name__


async def readiness_endpoint(request: Request) -> Response:
    """Deep readiness probe: database reachability plus code-index stats.

    ``/v1/health`` stays a static liveness signal; this endpoint is deliberately
    strict enough to fail when PostgreSQL is unreachable, so the pod is removed from
    the Service instead of being restarted.
    """
    state = request.app.state
    draining = bool(getattr(state, "draining", False))
    db_manager = getattr(state, "db_manager", None)
    if db_manager is None:
        db_ok, detail = True, "not_configured"
    else:
        db_ok, detail = await _ping_database(db_manager)

    stats = _code_index_stats(state)
    code_index = {key: stats[key] for key in sorted(stats)} if stats else {}
    status = "draining" if draining else ("ready" if db_ok else "degraded")
    payload = {
        "status": status,
        "database": {"ok": db_ok, "detail": detail},
        "code_index": code_index,
        "draining": draining,
    }
    return JSONResponse(status_code=200 if (db_ok and not draining) else 503, content=payload)


async def drain_endpoint(request: Request) -> Response:
    """Flip the pod into draining mode ahead of termination.

    Kubernetes runs the ``preStop`` hook before sending SIGTERM: the hook calls this
    endpoint, which makes ``/v1/ready`` fail so the endpoint is removed from the
    Service while in-flight turns keep the pod alive for the grace period.
    """
    request.app.state.draining = True
    begin_shutdown()
    logger.info("Drain requested; /v1/ready will report draining until termination.")
    return JSONResponse(status_code=200, content={"status": "draining"})


# ---------------------------------------------------------------------------
# Backpressure primitives.
# ---------------------------------------------------------------------------


class GlobalConcurrencyGate:
    """Bounds concurrent turns for the whole process.

    The orchestrator's per-session ``_active_tasks`` guard only prevents a second
    turn on the *same* session; nothing stops N sessions from each starting a turn
    and saturating the pod. This gate adds the missing global ceiling and reports the
    waiting depth so operators can size ``ROCKET_GLOBAL_CONCURRENCY_LIMIT``.
    """

    def __init__(self, limit: int) -> None:
        self.limit = max(1, limit)
        self._semaphore = asyncio.Semaphore(self.limit)
        self.active = 0
        self.queued = 0
        self._held: ContextVar[bool] = ContextVar("concurrency_slot_held", default=False)

    async def _acquire(self) -> None:
        self.queued += 1
        try:
            await self._semaphore.acquire()
        finally:
            self.queued = max(0, self.queued - 1)
        self.active += 1

    def _release(self) -> None:
        self.active = max(0, self.active - 1)
        self._semaphore.release()

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        await self._acquire()
        token = self._held.set(True)
        try:
            yield
        finally:
            if self._held.get():
                self._release()
            self._held.reset(token)

    @asynccontextmanager
    async def suspended(self) -> AsyncIterator[None]:
        """Hand the current turn's slot back while it waits for a human, then re-acquire it.

        A turn paused on an approval or question can wait for hours; holding its slot would
        let ``limit`` such turns starve every other session. No-op outside a slot.
        """
        if not self._held.get():
            yield
            return
        self._held.set(False)
        self._release()
        try:
            yield
        finally:
            await self._acquire()
            self._held.set(True)


class SessionWriteLocks:
    """A bounded registry of per-session single-writer locks.

    A session must never run two turns at once. This is the
    bootstrap-layer registry the orchestrator can adopt at turn entry; it is bounded
    so a long-lived pod serving many one-off sessions cannot retain a lock per
    session forever.
    """

    def __init__(self, capacity: int = DEFAULT_SESSION_LOCK_CAPACITY) -> None:
        self.capacity = max(1, capacity)
        self._locks: OrderedDict[str, asyncio.Lock] = OrderedDict()

    @property
    def size(self) -> int:
        return len(self._locks)

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is not None:
            self._locks.move_to_end(session_id)
            return lock
        if len(self._locks) >= self.capacity:
            self._evict_idle()
        lock = asyncio.Lock()
        self._locks[session_id] = lock
        return lock

    def _evict_idle(self) -> None:
        """Drop the oldest unlocked entry to stay within capacity.

        A locked entry is never evicted: evicting it would let a second writer enter
        the same session, which is the exact race this registry exists to prevent.
        """
        for key in list(self._locks):
            if not self._locks[key].locked():
                del self._locks[key]
                return

    @asynccontextmanager
    async def lock(self, session_id: str) -> AsyncIterator[None]:
        lock = self._lock_for(session_id)
        async with lock:
            yield


def build_backpressure(
    *, limit: int | None = None, lock_capacity: int | None = None
) -> tuple[GlobalConcurrencyGate, SessionWriteLocks]:
    """Construct the process-wide gate and lock registry for the app lifespan."""
    gate = GlobalConcurrencyGate(limit if limit is not None else global_concurrency_limit())
    locks = SessionWriteLocks(
        lock_capacity if lock_capacity is not None else session_lock_capacity()
    )
    TURN_CONCURRENCY_LIMIT.set(None, float(gate.limit))
    return gate, locks
