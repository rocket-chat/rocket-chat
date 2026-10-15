"""The GitHub tools that replace `gh`: pull requests, issues, Actions, releases, search, any API.

GitHub is a recorded fake (``httpx.MockTransport``). Checked: reads use the turn's read
credential and changes the write credential resolved for the repository; results say who acted
and never contain a token; administrative endpoints are refused; and which calls need a person
is decided per action.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.permissions import approval_needed
from agent_core.tools import github_client
from agent_core.tools.registry import ToolRegistry

from specifications.interfaces.agent import GithubAccess, TurnCallbacks, TurnPolicy

READ, WRITE = "ghs_read_token", "ghs_write_token"


async def _approve(action_type: str, description: str, risk_level: str) -> str:
    return "Approved by the user: proceed."


class FakeGitHub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        self.calls.append((request.method, request.url.path, token, body))
        return self.route(request.method, request.url.path, dict(request.url.params), body)

    def route(self, method: str, path: str, params: dict[str, str], body: Any) -> httpx.Response:
        if path.endswith("/graphql"):
            query = body["query"]
            if "reviewThreads" in query:
                thread = {
                    "id": "PRRT_1",
                    "isResolved": False,
                    "path": "app.py",
                    "line": 3,
                    "comments": {
                        "nodes": [
                            {"databaseId": 77, "author": {"login": "rev"}, "body": "Rename x"}
                        ]
                    },
                }
                data = {"repository": {"pullRequest": {"reviewThreads": {"nodes": [thread]}}}}
                return httpx.Response(200, json={"data": data})
            return httpx.Response(200, json={"data": {"ok": True}})
        if path == "/repos/acme/app/pulls/7" and method == "GET":
            return httpx.Response(
                200,
                json={
                    "node_id": "PR_7",
                    "head": {"sha": "abc12345", "ref": "f"},
                    "base": {"ref": "main"},
                },
            )
        if path == "/repos/acme/app/pulls/7/files":
            files = [
                {
                    "filename": f"f{n}.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 0,
                    "patch": f"@@ +{n}",
                }
                for n in range(12)
            ]
            return httpx.Response(200, json=files)
        if path == "/repos/acme/app/commits/abc12345/check-runs":
            runs = [
                {"id": 5, "name": "test", "conclusion": "failure"},
                {"id": 6, "name": "lint", "conclusion": "success"},
            ]
            return httpx.Response(200, json={"check_runs": runs})
        if path == "/repos/acme/app/actions/jobs/9/logs":
            log = "\n".join(f"line {n}" for n in range(1000)) + "\nError: assertion failed"
            return httpx.Response(200, text=log, headers={"content-type": "text/plain"})
        if path.endswith("/issues") and method == "POST":
            return httpx.Response(
                201, json={"number": 12, "html_url": "https://github.com/acme/app/issues/12"}
            )
        if path.endswith("/comments") and method == "POST":
            return httpx.Response(201, json={"html_url": "https://github.com/acme/app/pull/7#c1"})
        if path.endswith("/merge"):
            return httpx.Response(200, json={"sha": "deadbeef0000ffff"})
        if path == "/repos/acme/app/labels" and params.get("page") not in (None, "1"):
            return httpx.Response(200, json=[{"name": f"l{params['page']}"}] * 100)
        if path == "/repos/acme/app/labels":
            return httpx.Response(200, json=[{"name": "bug"}] * 100)
        return httpx.Response(200, json={"ok": True})


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    fake = FakeGitHub()
    real = httpx.AsyncClient
    monkeypatch.setattr(
        github_client.httpx,
        "AsyncClient",
        lambda **kwargs: real(transport=httpx.MockTransport(fake.handler), **kwargs),
    )
    return fake


@pytest.fixture
def registry() -> Iterator[ToolRegistry]:
    async def read() -> GithubAccess:
        return GithubAccess(READ, actor="the App (read)")

    async def write(repository: str) -> GithubAccess:
        assert repository == "acme/app", "the credential is resolved for the repository acted on"
        return GithubAccess(WRITE, actor="@octocat")

    ctx = ExecutionContext(
        session_id="s",
        event_queue=asyncio.Queue(),
        parent_model="m",
        callbacks=TurnCallbacks(github_access=read, github_write_access=write, approval=_approve),
    )
    token = current_execution_ctx.set(ctx)
    yield ToolRegistry()
    current_execution_ctx.reset(token)


async def test_reads_use_the_read_credential_and_changes_the_write_one(
    registry: ToolRegistry, github: FakeGitHub
) -> None:
    checks = await registry.call_tool(
        "github_pull_request", {"repository": "acme/app", "action": "checks", "number": 7}
    )
    commented = await registry.call_tool(
        "github_pull_request",
        {"repository": "acme/app", "action": "comment", "number": 7, "body": "Fixed in 2f3a"},
    )

    assert "1 failing" in checks and "github_actions job_logs" in checks
    assert commented.startswith("Commented on #7") and commented.endswith("(as @octocat)")
    assert {(m, t) for m, p, t, _ in github.calls if m == "GET"} == {("GET", READ)}
    assert [(m, t) for m, p, t, _ in github.calls if m == "POST"] == [("POST", WRITE)]
    assert READ not in checks + commented and WRITE not in checks + commented


async def test_review_threads_reply_and_resolve(registry: ToolRegistry, github: FakeGitHub) -> None:
    threads = await registry.call_tool(
        "github_pull_request", {"repository": "acme/app", "action": "review_threads", "number": 7}
    )
    await registry.call_tool(
        "github_pull_request",
        {
            "repository": "acme/app",
            "action": "reply_to_thread",
            "number": 7,
            "comment_id": 77,
            "body": "Done",
        },
    )
    await registry.call_tool(
        "github_pull_request",
        {"repository": "acme/app", "action": "resolve_thread", "number": 7, "thread_id": "PRRT_1"},
    )

    assert "thread PRRT_1 [open] app.py:3" in threads and "comment 77 by rev: Rename x" in threads
    paths = [p for m, p, _, _ in github.calls if m == "POST"]
    assert "/repos/acme/app/pulls/7/comments/77/replies" in paths
    assert any("resolveReviewThread" in json.dumps(b) for _, _, _, b in github.calls if b)


async def test_the_diff_is_read_ten_files_at_a_time(
    registry: ToolRegistry, github: FakeGitHub
) -> None:
    second = await registry.call_tool(
        "github_pull_request", {"repository": "acme/app", "action": "diff", "number": 7, "page": 2}
    )

    assert "--- f10.py" in second and "--- f9.py" not in second
    assert "[files 11-12 of 12; page 2 of 2]" in second


async def test_a_jobs_log_is_read_from_its_end(registry: ToolRegistry, github: FakeGitHub) -> None:
    logs = await registry.call_tool(
        "github_actions", {"repository": "acme/app", "action": "job_logs", "job_id": 9}
    )

    assert logs.startswith("[last 300 of 1001 log lines of job 9]")
    assert "Error: assertion failed" in logs and "line 10\n" not in logs


async def test_issues_can_be_opened_and_commented(
    registry: ToolRegistry, github: FakeGitHub
) -> None:
    opened = await registry.call_tool(
        "github_issues",
        {"repository": "acme/app", "action": "create", "title": "Flaky test", "labels": ["ci"]},
    )

    assert "Opened issue #12" in opened and opened.endswith("(as @octocat)")
    created = [b for m, p, _, b in github.calls if m == "POST" and p == "/repos/acme/app/issues"]
    assert created == [{"title": "Flaky test", "body": "", "labels": ["ci"]}]


@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/repos/acme/app/actions/secrets"),
        ("GET", "/repos/acme/app/hooks"),
        ("POST", "/repos/acme/app/keys"),
        ("PUT", "/repos/acme/app/branches/main/protection"),
        ("DELETE", "/repos/acme/app"),
        ("PATCH", "/repos/acme/app"),
        ("PUT", "/repos/acme/app/collaborators/mallory"),
        ("PATCH", "/orgs/acme"),
        ("GET", "/app/installations"),
        ("POST", "/repos/acme/app/rulesets"),
    ],
)
async def test_administrative_endpoints_are_refused(
    registry: ToolRegistry, github: FakeGitHub, method: str, path: str
) -> None:
    out = await registry.call_tool("github_api", {"method": method, "path": path})

    assert out.startswith("Error:") and github.calls == []


@pytest.mark.parametrize("path", ["https://evil.example/x", "/repos/../x", "//x", "repos/a/b"])
async def test_only_api_paths_are_accepted(
    registry: ToolRegistry, github: FakeGitHub, path: str
) -> None:
    assert (await registry.call_tool("github_api", {"path": path})).startswith("Error:")


async def test_the_generic_tool_reads_pages_and_writes_with_the_repositorys_credential(
    registry: ToolRegistry, github: FakeGitHub
) -> None:
    listed = await registry.call_tool(
        "github_api", {"path": "/repos/acme/app/labels", "paginate": True}
    )
    added = await registry.call_tool(
        "github_api",
        {"method": "POST", "path": "/repos/acme/app/labels", "body": {"name": "triage"}},
    )
    mutation = await registry.call_tool(
        "github_api", {"graphql": "mutation { addStar(input: {}) { clientMutationId } }"}
    )
    denied = await registry.call_tool(
        "github_api",
        {"graphql": "mutation { deleteRepository(input: {}) { x } }", "repository": "acme/app"},
    )

    assert len([c for c in github.calls if c[1] == "/repos/acme/app/labels" and c[0] == "GET"]) == 5
    assert "l5" in listed
    assert added.startswith("POST /repos/acme/app/labels done (as @octocat)")
    assert mutation.startswith("Error:") and "needs the repository" in mutation
    assert denied.startswith("Error:")


@pytest.mark.parametrize(
    "mode, name, arguments, gated",
    [
        ("auto", "github_pull_request", {"action": "view"}, False),
        ("auto", "github_pull_request", {"action": "comment"}, False),
        ("ask", "github_pull_request", {"action": "comment"}, True),
        ("auto", "github_pull_request", {"action": "merge"}, True),
        ("bypass", "github_pull_request", {"action": "merge"}, False),
        ("auto", "github_api", {"method": "GET"}, False),
        ("auto_edit", "github_api", {"method": "POST"}, True),
        ("auto", "github_api", {"method": "DELETE"}, True),
        ("auto", "github_api", {"graphql": "query { viewer { login } }"}, False),
        ("auto", "github_issues", {}, False),
        ("ask", "github_issues", {"action": "create"}, True),
        ("ask", "github_search", {}, False),
    ],
)
def test_who_must_approve_is_decided_per_action(
    mode: str, name: str, arguments: dict[str, Any], gated: bool
) -> None:
    assert (approval_needed(mode, name, arguments) is not None) is gated


async def test_plan_mode_allows_github_reads_and_blocks_changes(github: FakeGitHub) -> None:
    async def read() -> GithubAccess:
        return GithubAccess(READ)

    ctx = ExecutionContext(
        session_id="s",
        event_queue=asyncio.Queue(),
        parent_model="m",
        plan_mode=True,
        policy=TurnPolicy(permission_mode="plan"),
        callbacks=TurnCallbacks(github_access=read),
    )
    token = current_execution_ctx.set(ctx)
    try:
        registry = ToolRegistry()
        read_out = await registry.call_tool(
            "github_pull_request", {"repository": "acme/app", "action": "checks", "number": 7}
        )
        blocked = await registry.call_tool(
            "github_pull_request",
            {"repository": "acme/app", "action": "comment", "number": 7, "body": "x"},
        )
    finally:
        current_execution_ctx.reset(token)

    assert "Checks on abc12345" in read_out
    assert blocked.startswith("Blocked:") and "plan mode" in blocked


async def test_without_a_write_credential_the_reason_is_given(github: FakeGitHub) -> None:
    async def write(repository: str) -> GithubAccess:
        return GithubAccess(None)

    ctx = ExecutionContext(
        session_id="s",
        event_queue=asyncio.Queue(),
        parent_model="m",
        callbacks=TurnCallbacks(github_write_access=write, approval=_approve),
    )
    token = current_execution_ctx.set(ctx)
    try:
        out = await ToolRegistry().call_tool(
            "github_pull_request",
            {"repository": "acme/app", "action": "merge", "number": 7},
        )
    finally:
        current_execution_ctx.reset(token)

    assert "No GitHub credential can make changes" in out and github.calls == []
