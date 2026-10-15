"""GitHub Actions, releases and search: what ``gh run``, ``gh release`` and ``gh search`` do."""

from __future__ import annotations

from typing import Any

from agent_core.tools.clamp import clamp_output
from agent_core.tools.github_client import GithubError, acted_as, request
from specifications.interfaces.agent import GithubAccess

ACTIONS_READ = frozenset({"runs", "run_view", "job_logs"})
ACTIONS_WRITE = frozenset({"rerun_failed", "cancel", "dispatch_workflow"})
RELEASES_READ = frozenset({"list", "view"})
RELEASES_WRITE = frozenset({"create", "edit"})
LOG_TAIL_LINES = 300


def _run_line(run: dict[str, Any]) -> str:
    return (
        f"run {run.get('id')} [{run.get('conclusion') or run.get('status')}] "
        f"{run.get('name')} on {run.get('head_branch')} ({str(run.get('head_sha', ''))[:8]}, "
        f"{run.get('event')})"
    )


async def runs(access: GithubAccess, slug: str, branch: str | None) -> str:
    params: dict[str, Any] = {"per_page": 15}
    if branch:
        params["branch"] = branch
    data = await request(access, "GET", f"/repos/{slug}/actions/runs", params=params)
    found = data.get("workflow_runs", [])
    return "\n".join(_run_line(r) for r in found) or "No workflow runs found."


async def run_view(access: GithubAccess, slug: str, run_id: int) -> str:
    run = await request(access, "GET", f"/repos/{slug}/actions/runs/{run_id}")
    jobs = await request(
        access, "GET", f"/repos/{slug}/actions/runs/{run_id}/jobs", params={"per_page": 100}
    )
    lines = [_run_line(run)]
    for job in jobs.get("jobs", []):
        failed = [s["name"] for s in job.get("steps", []) if s.get("conclusion") == "failure"]
        tail = f" (failed step: {', '.join(failed)})" if failed else ""
        lines.append(
            f"  job {job.get('id')} [{job.get('conclusion') or job.get('status')}] {job.get('name')}{tail}"
        )
    return "\n".join(lines)


async def job_logs(access: GithubAccess, slug: str, job_id: int) -> str:
    """A job's log, its end first: a failure is explained at the end of its log."""
    text = await request(access, "GET", f"/repos/{slug}/actions/jobs/{job_id}/logs", accept="*/*")
    lines = str(text or "").splitlines()
    tail = lines[-LOG_TAIL_LINES:]
    head = f"[last {len(tail)} of {len(lines)} log lines of job {job_id}]\n"
    return head + clamp_output("\n".join(tail))


async def write_run(access: GithubAccess, slug: str, action: str, fields: dict[str, Any]) -> str:
    if action == "dispatch_workflow":
        workflow, ref = fields.get("workflow"), fields.get("ref")
        if not workflow or not ref:
            raise GithubError("Give the workflow (file name or id) and the ref to run it on.")
        await request(
            access,
            "POST",
            f"/repos/{slug}/actions/workflows/{workflow}/dispatches",
            body={"ref": ref, "inputs": fields.get("inputs") or {}},
        )
        return f"Started workflow {workflow} on {ref}{acted_as(access)}"
    run_id = fields.get("run_id")
    if not run_id:
        raise GithubError("Give the run_id (from runs).")
    suffix = "rerun-failed-jobs" if action == "rerun_failed" else "cancel"
    await request(access, "POST", f"/repos/{slug}/actions/runs/{int(run_id)}/{suffix}")
    verb = "Re-running the failed jobs of" if action == "rerun_failed" else "Cancelled"
    return f"{verb} run {run_id}{acted_as(access)}"


async def releases(access: GithubAccess, slug: str, tag: str | None) -> str:
    if tag:
        release = await request(access, "GET", f"/repos/{slug}/releases/tags/{tag}")
        return f"{release.get('tag_name')}: {release.get('name')}\n" + clamp_output(
            release.get("body") or "", 6000
        )
    found = await request(access, "GET", f"/repos/{slug}/releases", params={"per_page": 15})
    return (
        "\n".join(
            f"{r.get('tag_name')} {r.get('name') or ''} ({str(r.get('published_at') or 'draft')[:10]})"
            for r in found
        )
        or "No releases."
    )


async def write_release(
    access: GithubAccess, slug: str, action: str, fields: dict[str, Any]
) -> str:
    body = {
        key: fields[key]
        for key in ("tag_name", "name", "body", "draft", "prerelease", "target_commitish")
        if fields.get(key) not in (None, "")
    }
    if action == "create":
        if not body.get("tag_name"):
            raise GithubError("Give the tag_name of the release.")
        made = await request(access, "POST", f"/repos/{slug}/releases", body=body)
        return (
            f"Created release {made.get('tag_name')}: {made.get('html_url', '')}{acted_as(access)}"
        )
    tag = fields.get("tag_name")
    if not tag:
        raise GithubError("Give the tag_name of the release to edit.")
    existing = await request(access, "GET", f"/repos/{slug}/releases/tags/{tag}")
    await request(access, "PATCH", f"/repos/{slug}/releases/{existing['id']}", body=body)
    return f"Updated release {tag}{acted_as(access)}"


SEARCH_KINDS = {"code": "/search/code", "issues": "/search/issues", "commits": "/search/commits"}


async def search(access: GithubAccess, kind: str, query: str, repository: str | None) -> str:
    path = SEARCH_KINDS.get(kind)
    if path is None:
        raise GithubError("kind is one of: code, issues, commits.")
    q = f"{query} repo:{repository}" if repository else query
    data = await request(access, "GET", path, params={"q": q, "per_page": 20})
    items = data.get("items", [])
    if not items:
        return f"No {kind} matched '{q}'."
    if kind == "code":
        lines = [f"{i['repository']['full_name']}:{i.get('path')}" for i in items]
    elif kind == "commits":
        lines = [
            f"{i['repository']['full_name']}@{str(i.get('sha'))[:8]} "
            + " ".join(((i.get("commit") or {}).get("message") or "").split())[:120]
            for i in items
        ]
    else:
        lines = [f"{i.get('html_url')} [{i.get('state')}] {i.get('title')}" for i in items]
    return f"{data.get('total_count', len(items))} matches (first {len(lines)}):\n" + "\n".join(
        lines
    )
