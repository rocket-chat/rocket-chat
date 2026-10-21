#!/usr/bin/env python3
"""Measure how the agent harness performs on real tasks, through a running instance's API.

The deterministic golden suite proves the harness behaves; it cannot say whether a real model
finishes a real task quickly and cheaply. This tool does, against any running Rocket Chat
(the evaluation Compose stack, or your own instance), using only its public REST API:

``bench``   runs each task of ``tests/harness/live/tasks.json`` in a new session, waits until the
            session is idle (every turn ended, no wait pending), verifies the result with a final
            turn that runs the task's hidden check, and writes the figures of each task: turns,
            steps, model and tool minutes, reasoning, fixed overhead, compactions, pushes, tokens
            and cost. With ``--baseline`` it fails when a task regresses past ``--tolerance``.

``replay``  starts the first request of an existing session again, in a new session with the
            same agent, and prints both sessions' figures side by side. Unless ``--allow-publish``
            is given, the prompt asks the agent not to push or open pull requests: replaying a
            ticket otherwise publishes real branches.

Both spend provider credit. Authenticate with a personal API token (Settings, API access).

Usage::

    uv run python scripts/harness_bench.py bench --api https://rocket.example --token $TOKEN \\
        --agent persona-123 --report live-report.json [--baseline tests/harness/live/baseline.json]
    uv run python scripts/harness_bench.py replay --api https://rocket.example --token $TOKEN \\
        --session session_3116245f7a
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

TASKS = Path(__file__).resolve().parents[1] / "tests" / "harness" / "live" / "tasks.json"
POLL_SECONDS = 10.0
REQUEST_TIMEOUT_SECONDS = 30.0
NO_PUBLISH_NOTE = (
    "\n\n(Benchmark replay: do not push, open or update pull requests, and do not post to "
    "external systems. Stop once the change is implemented and tested in the sandbox.)"
)
# Figures where higher is worse, compared against the baseline.
REGRESSION_FIGURES = ("steps", "minutes", "cost_usd")
VERIFY_PASSED = "BENCH_VERIFY_PASSED"


class Api:
    def __init__(self, base: str, token: str | None) -> None:
        # No token: an API started with DEV_AUTH_BYPASS (the CI benchmark) takes anonymous calls.
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(
            base_url=base.rstrip("/"), headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
        )

    def get(self, path: str) -> Any:
        response = self._client.get(path)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, body: dict[str, Any]) -> Any:
        response = self._client.post(path, json=body)
        response.raise_for_status()
        return response.json()


def _start_session(api: Api, title: str, agent: str | None, model: str | None) -> str:
    body: dict[str, Any] = {"title": title}
    if agent:
        body["agent_id"] = agent
    if model:
        body["model"] = model
    return str(api.post("/v1/sessions", body)["session_id"])


def _send(api: Api, session_id: str, prompt: str) -> None:
    api.post(f"/v1/sessions/{session_id}/turns", {"prompt": prompt})


def _wait_idle(api: Api, session_id: str, deadline: float) -> bool:
    """Whether the session went idle before ``deadline``: no turn running, no wait pending.

    A pending wait (CI, a process, a timer) wakes the session again, so a session that is
    between turns but still waiting has not finished its task.
    """
    # A turn just sent may not be marked processing yet.
    time.sleep(POLL_SECONDS)
    while time.time() < deadline:
        session = api.get(f"/v1/sessions/{session_id}")
        waits = api.get(f"/v1/sessions/{session_id}/waits")
        pending = [w for w in waits if w.get("status") in ("waiting", "claimed")]
        if not session.get("is_processing") and not pending:
            return True
        time.sleep(POLL_SECONDS)
    return False


def _traces(api: Api, session_id: str) -> list[dict[str, Any]]:
    page = api.get(f"/v1/sessions/{session_id}/turns?limit=1000")
    return list(page.get("turns") or [])


def _figures(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """The task's figures, summed over its turns."""
    totals: dict[str, Any] = {"turns": len(traces), "minutes": 0.0, "cost_usd": 0.0}
    for trace in traces:
        if trace.get("ended_at"):
            totals["minutes"] += (trace["ended_at"] - trace["started_at"]) / 60
        tokens = trace.get("tokens") or {}
        usage = tokens.get("usage") or {}
        totals["cost_usd"] += float(usage.get("cost_usd") or 0)
        for key in ("prompt_tokens", "completion_tokens"):
            totals[key] = totals.get(key, 0) + int(usage.get(key) or 0)
        for key, value in (tokens.get("metrics") or {}).items():
            if key in ("overhead_tokens", "max_step_reasoning_chars"):
                totals[key] = max(totals.get(key, 0), int(value))
            else:
                totals[key] = totals.get(key, 0) + int(value)
    totals["minutes"] = round(totals["minutes"], 1)
    totals["cost_usd"] = round(totals["cost_usd"], 4)
    return totals


def _verify(api: Api, session_id: str, task: dict[str, Any], deadline: float) -> bool:
    """Run the task's hidden check in the session's sandbox and read its result from the trace.

    The result is read from the tool call's output, not from what the model says about it.
    """
    command = f"{task['verify']} && echo {VERIFY_PASSED}"
    _send(
        api,
        session_id,
        "Benchmark check. Make exactly one `bash_exec` call with "
        f"`cwd` set to `{task['workdir']}` and this command, unchanged, then answer with its "
        f"last line only:\n\n```bash\n{command}\n```",
    )
    if not _wait_idle(api, session_id, deadline):
        return False
    last = _traces(api, session_id)[-1]
    outputs = [
        str(call.get("result") or "")
        for call in last.get("tool_calls") or []
        if call.get("name") == "bash_exec"
    ]
    return any(VERIFY_PASSED in output for output in outputs)


def _run_task(api: Api, task: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    deadline = time.time() + args.task_minutes * 60
    session_id = _start_session(api, f"bench: {task['id']}", args.agent, args.model)
    _send(api, session_id, task["prompt"])
    finished = _wait_idle(api, session_id, deadline)
    figures = _figures(_traces(api, session_id))
    passed = finished and _verify(api, session_id, task, deadline + 600)
    return {"task": task["id"], "session": session_id, "passed": passed, **figures}


def _regressions(
    report: list[dict[str, Any]], baseline: list[dict[str, Any]], tolerance: float
) -> list[str]:
    known = {row["task"]: row for row in baseline}
    found: list[str] = []
    for row in report:
        if not row["passed"]:
            found.append(f"{row['task']}: failed its check")
        if row.get("compactions_without_model_summary"):
            found.append(f"{row['task']}: a compaction had no model summary")
        before = known.get(row["task"])
        if before is None:
            continue
        for figure in REGRESSION_FIGURES:
            if before.get(figure) and row.get(figure, 0) > before[figure] * tolerance:
                found.append(f"{row['task']}: {figure} {before[figure]} -> {row[figure]}")
    return found


def bench(args: argparse.Namespace) -> int:
    api = Api(args.api, args.token)
    tasks = json.loads(Path(args.tasks).read_text())["tasks"]
    if args.only:
        tasks = [t for t in tasks if t["id"] in args.only]
    report = []
    for task in tasks:
        row = _run_task(api, task, args)
        print(json.dumps(row), flush=True)
        report.append(row)
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
    if not args.baseline:
        return 0
    problems = _regressions(report, json.loads(Path(args.baseline).read_text()), args.tolerance)
    for problem in problems:
        print(f"REGRESSION {problem}", file=sys.stderr)
    return 1 if problems else 0


def _first_request(session: dict[str, Any]) -> str:
    for message in session.get("conversation_history") or []:
        if message.get("role") == "user" and message.get("content"):
            return str(message["content"])
    raise SystemExit("the session has no user message to replay")


def replay(args: argparse.Namespace) -> int:
    api = Api(args.api, args.token)
    source = api.get(f"/v1/sessions/{args.session}")
    prompt = _first_request(source)
    if not args.allow_publish:
        prompt += NO_PUBLISH_NOTE
    agent = args.agent or source.get("agent_id") or (source.get("metadata") or {}).get("agent_id")
    session_id = _start_session(api, f"replay: {source.get('title', args.session)}", agent, None)
    _send(api, session_id, prompt)
    finished = _wait_idle(api, session_id, time.time() + args.task_minutes * 60)
    before = _figures(_traces(api, args.session))
    after = _figures(_traces(api, session_id))
    print(f"replay session: {session_id} ({'idle' if finished else 'still running'})")
    print(f"{'figure':<36}{'original':>14}{'replay':>14}")
    for key in sorted(set(before) | set(after)):
        print(f"{key:<36}{before.get(key, 0)!s:>14}{after.get(key, 0)!s:>14}")
    return 0 if finished else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure the agent harness on real tasks.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("bench", "replay"):
        command = sub.add_parser(name)
        command.add_argument("--api", required=True, help="The instance's base URL")
        command.add_argument("--token", help="A personal API token (none: DEV_AUTH_BYPASS)")
        command.add_argument("--agent", help="Agent id (replay: defaults to the session's)")
        command.add_argument("--task-minutes", type=float, default=60.0)
    run = sub.choices["bench"]
    run.add_argument("--tasks", default=str(TASKS))
    run.add_argument("--only", nargs="*", help="Task ids to run (default: all)")
    run.add_argument("--model", help="Model for every task (default: the agent's)")
    run.add_argument("--report", default="live-report.json")
    run.add_argument("--baseline", help="A previous report to compare with")
    run.add_argument("--tolerance", type=float, default=1.25)
    again = sub.choices["replay"]
    again.add_argument("--session", required=True, help="The session to replay")
    again.add_argument("--allow-publish", action="store_true")
    args = parser.parse_args()
    return bench(args) if args.command == "bench" else replay(args)


if __name__ == "__main__":
    raise SystemExit(main())
