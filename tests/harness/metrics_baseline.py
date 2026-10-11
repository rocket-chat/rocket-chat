"""Capture the instrumentation baseline (time-to-first-token and cache hit ratio).

The deterministic golden suite scores *observable state*. This module captures the
other half: the two values instrumented on ``/metrics`` — time-to-first-token
(TTFT) histograms split by turn kind and the prompt-cache hit-ratio gauge. It drives a
conversational turn and a sandbox (tool) turn through the **real** turn handler
(``execute_turn_background``) against the stub gateway and fake sandbox, then reads the
Prometheus exposition.

The stub sleeps ``chunk_delay_seconds`` before its first token, so TTFT is a real
(non-zero), measurable duration rather than a zeroed histogram; the value is a property
of the deterministic harness, not of any provider, and is recorded as such.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.events import AsyncIOEventBus
from api.main import app
from api.services.metrics import render_prometheus
from api.store import InMemorySessionStore
from api.turns.runner import execute_turn_background
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.session import SessionRecord
from tests.harness.golden_tasks import DEFAULT_MODEL, TOOL_MARKER, GoldenStubGateway

STUB_CHUNK_DELAY_SECONDS = 0.05
TTFT_TARGET_SECONDS = {"conversational": 2.0, "sandbox": 6.0}


async def _drive_one(prompt: str) -> None:
    session_id = f"baseline_{uuid.uuid4().hex[:8]}"
    driver = FakeSandboxDriver()
    gateway = GoldenStubGateway(chunk_delay_seconds=STUB_CHUNK_DELAY_SECONDS)
    registry = ToolRegistry(driver=driver, session_id=session_id)
    orchestrator = AsyncReActOrchestrator(
        gateway=gateway, registry=registry, default_model=DEFAULT_MODEL
    )
    store = InMemorySessionStore()
    await store.create_session(
        SessionRecord(
            session_id=session_id,
            tenant_org_id="default_org",
            tenant_user_id="dev_user",
            title="Evaluation baseline",
        )
    )
    await execute_turn_background(
        session_id=session_id,
        prompt=prompt,
        orchestrator=orchestrator,
        event_bus=AsyncIOEventBus(),
        driver=driver,
        registry=registry,
        store=store,
        db_mgr=None,
    )
    app.state.llm_gateway = gateway


def _parse_ttft_histogram(text: str) -> dict[str, dict[str, Any]]:
    """Parse ``rocket_turn_time_to_first_token_seconds`` buckets per turn kind."""
    pattern = re.compile(
        r'^rocket_turn_time_to_first_token_seconds_bucket\{kind="([^"]+)",le="([^"]+)"\} ([0-9.]+)$'
    )
    histograms: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        kind, boundary, count = match.group(1), match.group(2), float(match.group(3))
        histograms.setdefault(kind, {"buckets": [], "count": 0.0})["buckets"].append(
            (boundary, count)
        )
    for histogram in histograms.values():
        for boundary, count in histogram["buckets"]:
            if boundary == "+Inf":
                histogram["count"] = count
    return histograms


def _p95(histogram: dict[str, Any]) -> float | None:
    """Return the smallest bucket boundary covering the 95th percentile."""
    count = float(histogram.get("count", 0.0))
    if count <= 0:
        return None
    threshold = 0.95 * count
    for boundary, cumulative in histogram["buckets"]:
        if boundary == "+Inf":
            return None
        if cumulative >= threshold:
            return float(boundary)
    return None


def capture_metrics_baseline() -> dict[str, Any]:
    """Drive both turn kinds through the app and return the observed instrumented values."""
    asyncio.run(_drive_one("Explain the retry policy."))
    asyncio.run(_drive_one(f"{TOOL_MARKER} list the workspace."))

    body = render_prometheus(app.state)
    histograms = _parse_ttft_histogram(body)

    match = re.search(r"^rocket_prompt_cache_hit_ratio ([0-9.]+)$", body, re.MULTILINE)
    cache_hit_ratio = float(match.group(1)) if match else None

    return {
        "ttft_p95_seconds": {kind: _p95(histograms.get(kind, {})) for kind in TTFT_TARGET_SECONDS},
        "ttft_targets_seconds": dict(TTFT_TARGET_SECONDS),
        "cache_hit_ratio": cache_hit_ratio,
        "cache_hit_target": 0.60,
        "stub_chunk_delay_seconds": STUB_CHUNK_DELAY_SECONDS,
        "series_present": {
            "ttft_histogram": "rocket_turn_time_to_first_token_seconds" in body,
            "cache_gauge": "rocket_prompt_cache_hit_ratio" in body,
        },
    }


def render_metrics_baseline(baseline: dict[str, Any]) -> str:
    """Render the captured baseline as a deterministic, dated-report-ready block."""
    ttft = baseline["ttft_p95_seconds"]
    targets = baseline["ttft_targets_seconds"]
    lines = ["METRICS BASELINE (driven through execute_turn_background, stub + fake sandbox)"]

    def _fmt(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.2f}s"

    for kind in ("conversational", "sandbox"):
        observed = ttft.get(kind)
        target = targets[kind]
        within = observed is not None and observed < target
        lines.append(
            f"  TTFT p95 {kind:<15} observed={_fmt(observed):>7}  target<{target:.1f}s  "
            f"within_target={within}"
        )
    cache = baseline["cache_hit_ratio"]
    cache_text = "n/a" if cache is None else f"{cache * 100:.1f}%"
    lines.append(
        f"  prompt-cache hit ratio observed={cache_text:>7}  "
        f"target>={baseline['cache_hit_target'] * 100:.0f}%"
    )
    lines.append(f"  deterministic stub chunk delay: {baseline['stub_chunk_delay_seconds']}s")
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_metrics_baseline(capture_metrics_baseline()))
