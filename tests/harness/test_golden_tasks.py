"""Golden-set evaluation harness tests.

These are the deterministic CI gate that keeps the task-completion claim honest: the golden suite must clear
the task-completion target, must be byte-stable across two consecutive runs, must
attribute tokens/cost per task, and the instrumentation must expose measurable TTFT
and cache-hit values.
"""

from __future__ import annotations

import pytest

from tests.harness.golden_tasks import (
    GoldenResult,
    completion_rate,
    run_golden_suite,
)
from tests.harness.metrics_baseline import capture_metrics_baseline

TASK_COMPLETION_TARGET = 0.80


def _columns(results: list[GoldenResult]) -> list[tuple[str, bool, int, int, float]]:
    """The deterministic (pass/fail, token, cost) projection that must be run-stable."""
    return [
        (r.task_id, r.passed, r.prompt_tokens, r.completion_tokens, r.cost_usd) for r in results
    ]


@pytest.mark.asyncio
async def test_golden_suite_clears_the_task_completion_target() -> None:
    results = await run_golden_suite()
    failed = [r.task_id for r in results if not r.passed]
    assert not failed, f"golden tasks failed (regressions): {failed}"
    assert completion_rate(results) >= TASK_COMPLETION_TARGET
    assert len(results) == 10, "the golden set is bounded to ten tasks"


@pytest.mark.asyncio
async def test_golden_suite_is_deterministic_across_runs() -> None:
    first = await run_golden_suite()
    second = await run_golden_suite()
    assert _columns(first) == _columns(second)


@pytest.mark.asyncio
async def test_golden_tasks_attribute_tokens_and_cost() -> None:
    results = await run_golden_suite()
    for result in results:
        assert result.total_tokens > 0, f"{result.task_id} recorded no tokens"
        assert result.cost_usd >= 0.0
    assert sum(r.cost_usd for r in results) > 0.0, "the ledger must estimate a non-zero cost"


def test_metrics_baseline_exposes_ttft_and_cache_hit_ratio() -> None:
    """Drive a conversational and a sandbox turn, then read the TTFT and cache values off /metrics."""
    baseline = capture_metrics_baseline()

    assert baseline["series_present"]["ttft_histogram"] is True
    assert baseline["series_present"]["cache_gauge"] is True

    ttft = baseline["ttft_p95_seconds"]
    for kind, target in baseline["ttft_targets_seconds"].items():
        observed = ttft[kind]
        assert observed is not None, f"no TTFT observations recorded for {kind}"
        assert observed < target, f"TTFT p95 {kind}={observed}s exceeded target {target}s"

    # The gauge is derived from the usage ledger and is computable, but the deterministic
    # stub never reports provider cache tokens, so the measured ratio is exactly 0. The
    # real-provider ≥60% target is descoped in the baseline report (see BASELINE.md).
    assert baseline["cache_hit_ratio"] == pytest.approx(0.0)
