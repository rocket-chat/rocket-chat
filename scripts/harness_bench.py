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

``ab``      runs the tasks with two configurations (``--a-agent``/``--a-model`` and ``--b-...``)
            and prints the pass rate and figures of each side by side.

``judge``   scores the answers of a report with a judge model (any LiteLLM model, with its
            provider key in the environment): correctness, scope, code quality and clarity,
            1 to 5 each, from the task, the agent's answer and its changes.

``dataset`` turns the organization's answer ratings (thumbs down first) into evaluation cases
            (JSON lines) that ``judge --cases`` scores the same way.

``bench`` and ``ab`` append each run's pass rate and totals to ``--history`` (JSON lines), so the
pass rate can be followed over time; ``history`` prints it.

Both spend provider credit. Authenticate with a personal API token (Settings, API access).

Usage::

    uv run python scripts/harness_bench.py bench --api https://rocket.example --token $TOKEN \\
        --agent persona-123 --report live-report.json [--baseline tests/harness/live/baseline.json]
    uv run python scripts/harness_bench.py replay --api https://rocket.example --token $TOKEN \\
        --session session_3116245f7a
    uv run python scripts/harness_bench.py ab --api ... --a-model claude-sonnet-5-5 \\
        --b-model claude-opus-5-5 --only go-uuid-must-parse-bytes
    uv run python scripts/harness_bench.py judge --api ... --report live-report.json \\
        --judge-model claude-opus-5-5
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


def _tasks(args: argparse.Namespace) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = json.loads(Path(args.tasks).read_text())["tasks"]
    return [t for t in tasks if t["id"] in args.only] if args.only else tasks


def _summary(report: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for row in report if row["passed"])
    return {
        "tasks": len(report),
        "passed": passed,
        "pass_rate": round(passed / len(report), 3) if report else 0.0,
        "cost_usd": round(sum(row.get("cost_usd", 0) for row in report), 4),
        "minutes": round(sum(row.get("minutes", 0) for row in report), 1),
        "steps": sum(row.get("steps", 0) for row in report),
    }


def _record_history(args: argparse.Namespace, label: str, report: list[dict[str, Any]]) -> None:
    if not args.history:
        return
    entry = {
        "at": int(time.time()),
        "label": label,
        "agent": getattr(args, "agent", None),
        "model": getattr(args, "model", None),
        **_summary(report),
    }
    with Path(args.history).open("a") as history:
        history.write(json.dumps(entry) + "\n")


def bench(args: argparse.Namespace) -> int:
    api = Api(args.api, args.token)
    report = []
    for task in _tasks(args):
        row = _run_task(api, task, args)
        print(json.dumps(row), flush=True)
        report.append(row)
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
    _record_history(args, "bench", report)
    print(json.dumps(_summary(report)))
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


def ab(args: argparse.Namespace) -> int:
    """The same tasks with two configurations, side by side."""
    api = Api(args.api, args.token)
    sides: dict[str, list[dict[str, Any]]] = {"a": [], "b": []}
    for task in _tasks(args):
        for side in ("a", "b"):
            config = argparse.Namespace(
                **{
                    **vars(args),
                    "agent": getattr(args, f"{side}_agent"),
                    "model": getattr(args, f"{side}_model"),
                }
            )
            row = {"side": side, **_run_task(api, task, config)}
            print(json.dumps(row), flush=True)
            sides[side].append(row)
    Path(args.report).write_text(json.dumps(sides, indent=2) + "\n")
    a, b = _summary(sides["a"]), _summary(sides["b"])
    _record_history(args, f"ab:a {args.a_agent or ''} {args.a_model or ''}".strip(), sides["a"])
    _record_history(args, f"ab:b {args.b_agent or ''} {args.b_model or ''}".strip(), sides["b"])
    print(f"{'figure':<12}{'A':>14}{'B':>14}")
    for key in a:
        print(f"{key:<12}{a[key]!s:>14}{b[key]!s:>14}")
    return 0


JUDGE_RUBRIC = (
    "You grade a coding agent's work. Score each criterion from 1 (bad) to 5 (excellent): "
    "correctness (does the change do what was asked, are the tests real), scope (only what "
    "was asked, nothing unrelated), quality (idiomatic, readable, tested), clarity (the answer "
    "says what changed and how it was checked). Answer with JSON only: "
    '{"correctness": n, "scope": n, "quality": n, "clarity": n, "notes": "one sentence"}.'
)
MAX_JUDGE_CHARS = 30_000


def _judge(model: str, task: str, answer: str, changes: str) -> dict[str, Any]:
    import litellm

    content = f"Task:\n{task}\n\nAgent's answer:\n{answer}\n\nFiles changed:\n{changes}"
    response = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": JUDGE_RUBRIC},
            {"role": "user", "content": content[:MAX_JUDGE_CHARS]},
        ],
        timeout=120,
    )
    text = str(response.choices[0].message.content or "")
    match = text[text.find("{") : text.rfind("}") + 1]
    try:
        scores: dict[str, Any] = json.loads(match)
    except ValueError:
        return {"error": "the judge gave no JSON", "raw": text[:300]}
    criteria = ("correctness", "scope", "quality", "clarity")
    scores["mean"] = round(sum(float(scores.get(c, 0)) for c in criteria) / len(criteria), 2)
    return scores


def _last_answer(session: dict[str, Any]) -> str:
    for message in reversed(session.get("conversation_history") or []):
        if message.get("role") == "assistant" and message.get("content"):
            return str(message["content"])
    return ""


def judge(args: argparse.Namespace) -> int:
    """Score each task of a report (or each case of a dataset) with the judge model."""
    scored = []
    if args.cases:
        for line in Path(args.cases).read_text().splitlines():
            case = json.loads(line)
            row = {"case": case.get("id"), "rating": case.get("rating")}
            row.update(_judge(args.judge_model, case["prompt"], case["answer"], ""))
            print(json.dumps(row), flush=True)
            scored.append(row)
    else:
        api = Api(args.api, args.token)
        prompts = {t["id"]: t["prompt"] for t in json.loads(Path(args.tasks).read_text())["tasks"]}
        report = json.loads(Path(args.report).read_text())
        rows = report if isinstance(report, list) else [*report["a"], *report["b"]]
        for row in rows:
            session = api.get(f"/v1/sessions/{row['session']}")
            changes = api.get(f"/v1/sessions/{row['session']}/changes")
            # The changed files and their sizes (the full diff is in the session, if needed).
            diff = "\n".join(
                f"{repo.get('name')}/{f.get('path')} ({f.get('status')}, +{f.get('additions')} -{f.get('deletions')})"
                for repo in changes.get("repositories") or []
                for f in repo.get("files") or []
            )
            scores = _judge(
                args.judge_model, prompts.get(row["task"], ""), _last_answer(session), diff
            )
            entry = {
                "task": row["task"],
                "side": row.get("side"),
                "passed": row["passed"],
                **scores,
            }
            print(json.dumps(entry), flush=True)
            scored.append(entry)
    Path(args.output).write_text(json.dumps(scored, indent=2) + "\n")
    means = [float(r["mean"]) for r in scored if "mean" in r]
    print(f"mean judge score: {round(sum(means) / len(means), 2) if means else 'n/a'}")
    return 0


def dataset(args: argparse.Namespace) -> int:
    """The organization's answer ratings as evaluation cases, thumbs down first."""
    api = Api(args.api, args.token)
    ratings = api.get(f"/v1/admin/feedback?since={args.since}")
    ratings = sorted(ratings, key=lambda r: r.get("rating") != "down")
    with Path(args.output).open("w") as out:
        for rating in ratings:
            case = {
                "id": f"{rating['session_id']}#{rating['turn_number']}",
                "rating": rating["rating"],
                "comment": rating.get("comment") or "",
                "prompt": rating.get("prompt_excerpt") or "",
                "answer": rating.get("answer_excerpt") or "",
                "model": rating.get("model"),
                "agent_id": rating.get("agent_id"),
            }
            out.write(json.dumps(case) + "\n")
    print(f"{len(ratings)} cases written to {args.output}")
    return 0


def history(args: argparse.Namespace) -> int:
    """The pass rate over time, oldest first."""
    entries = [json.loads(line) for line in Path(args.history).read_text().splitlines() if line]
    print(f"{'when (UTC)':<18}{'label':<34}{'pass':>8}{'rate':>7}{'cost':>10}")
    for e in entries:
        when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(e["at"]))
        print(
            f"{when:<18}{e['label'][:33]:<34}{e['passed']:>4}/{e['tasks']:<3}{e['pass_rate']:>7}{e['cost_usd']:>10}"
        )
    return 0


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
    run.add_argument("--history", help="Append this run's pass rate to a JSON-lines file")
    again = sub.choices["replay"]
    again.add_argument("--session", required=True, help="The session to replay")
    again.add_argument("--allow-publish", action="store_true")
    compare = sub.add_parser("ab")
    compare.add_argument("--api", required=True)
    compare.add_argument("--token")
    compare.add_argument("--task-minutes", type=float, default=60.0)
    compare.add_argument("--tasks", default=str(TASKS))
    compare.add_argument("--only", nargs="*")
    for side in ("a", "b"):
        compare.add_argument(f"--{side}-agent")
        compare.add_argument(f"--{side}-model")
    compare.add_argument("--report", default="ab-report.json")
    compare.add_argument("--history")
    grade = sub.add_parser("judge")
    grade.add_argument("--api")
    grade.add_argument("--token")
    grade.add_argument("--tasks", default=str(TASKS))
    grade.add_argument("--report", default="live-report.json")
    grade.add_argument("--cases", help="A dataset (JSON lines) to score instead of a report")
    grade.add_argument("--judge-model", required=True)
    grade.add_argument("--output", default="judge-report.json")
    export = sub.add_parser("dataset")
    export.add_argument("--api", required=True)
    export.add_argument("--token")
    export.add_argument("--since", type=int, default=0)
    export.add_argument("--output", default="feedback-cases.jsonl")
    trend = sub.add_parser("history")
    trend.add_argument("--history", required=True)
    args = parser.parse_args()
    commands = {
        "bench": bench,
        "replay": replay,
        "ab": ab,
        "judge": judge,
        "dataset": dataset,
        "history": history,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
