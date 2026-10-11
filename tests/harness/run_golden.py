"""CLI runner for the deterministic golden-task evaluation harness.

Usage (from the repository root, inside the uv environment)::

    uv run python -m tests.harness.run_golden              # deterministic golden report
    uv run python -m tests.harness.run_golden --twice      # run twice, assert byte-identical
    uv run python -m tests.harness.run_golden --metrics    # also capture the metrics baseline

The golden report is a pure function of the stub gateway and fake sandbox, so two runs
produce byte-identical pass/fail and cost/token columns. ``--metrics`` is deliberately a
separate flag because it is wall-clock-based and therefore not byte-stable.
"""

from __future__ import annotations

import argparse
import asyncio

from tests.harness.golden_tasks import render_report, run_golden_suite


async def _report() -> str:
    return render_report(await run_golden_suite())


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the golden-task evaluation harness.")
    parser.add_argument(
        "--twice",
        action="store_true",
        help="Run the suite twice and fail (exit 1) if the two reports differ.",
    )
    parser.add_argument(
        "--metrics",
        action="store_true",
        help="Also capture the TTFT / prompt-cache metrics baseline through the app.",
    )
    args = parser.parse_args()

    first = asyncio.run(_report())
    print(first)

    if args.twice:
        second = asyncio.run(_report())
        if first != second:
            print("\nDETERMINISM CHECK: FAIL (reports differ between runs)", flush=True)
            return 1
        print("\nDETERMINISM CHECK: PASS (identical reports)", flush=True)

    if args.metrics:
        from tests.harness.metrics_baseline import capture_metrics_baseline, render_metrics_baseline

        print("\n" + render_metrics_baseline(capture_metrics_baseline()))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
