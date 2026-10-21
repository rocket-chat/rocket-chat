"""The live benchmark sums a task's turns and flags regressions against a baseline."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.harness_bench import TASKS, _figures, _regressions


def _trace(start: int, end: int, cost: float, metrics: dict[str, int]) -> dict[str, object]:
    usage = {"cost_usd": cost, "prompt_tokens": 1000, "completion_tokens": 100}
    return {"started_at": start, "ended_at": end, "tokens": {"usage": usage, "metrics": metrics}}


def test_a_tasks_figures_sum_its_turns_and_keep_the_largest_overhead() -> None:
    figures = _figures(
        [
            _trace(0, 600, 0.5, {"steps": 20, "overhead_tokens": 9000, "pushes": 1}),
            _trace(900, 1200, 0.25, {"steps": 5, "overhead_tokens": 8000, "compactions": 1}),
        ]
    )

    assert figures["turns"] == 2 and figures["minutes"] == 15.0 and figures["cost_usd"] == 0.75
    assert figures["steps"] == 25 and figures["overhead_tokens"] == 9000
    assert figures["pushes"] == 1 and figures["compactions"] == 1
    assert figures["prompt_tokens"] == 2000


def test_a_failed_check_a_lost_summary_and_a_slower_task_are_regressions() -> None:
    baseline = [{"task": "t", "steps": 20, "minutes": 10.0, "cost_usd": 0.5}]
    lost_summary = {"compactions_without_model_summary": 1}
    report = [{"task": "t", "passed": False, "steps": 30, "minutes": 10.0, "cost_usd": 0.5,
               **lost_summary}]  # fmt: skip

    found = _regressions(report, baseline, tolerance=1.25)

    assert "t: failed its check" in found
    assert "t: a compaction had no model summary" in found
    assert "t: steps 20 -> 30" in found and not any("minutes" in f for f in found)


def test_every_task_has_a_prompt_a_working_folder_and_a_check() -> None:
    tasks = json.loads(Path(TASKS).read_text())["tasks"]
    assert len({t["id"] for t in tasks}) == len(tasks) >= 3
    for task in tasks:
        assert task["prompt"] and task["workdir"].startswith("/workspace/") and task["verify"]
        assert "Do not push" in task["prompt"], "a benchmark task never publishes"
