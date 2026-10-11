"""Read-only GitHub tools: find repositories, read files, issues and pull requests."""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import httpx
import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.permissions import approval_needed
from agent_core.tools import github_tools
from agent_core.tools.registry import ToolRegistry

from specifications.interfaces.agent import GithubAccess, TurnCallbacks

_REAL_CLIENT = httpx.AsyncClient
TOKEN = "ghu_" + "s" * 36


def _github(
    monkeypatch: pytest.MonkeyPatch, routes: dict[str, Any], status: dict[str, int] | None = None
) -> list[httpx.Request]:
    """Stub GitHub: ``routes`` maps a path to a JSON body; returns the requests seen."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        code = (status or {}).get(path, 200 if path in routes else 404)
        return httpx.Response(code, json=routes.get(path, {"message": "Not Found"}))

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kw),
    )
    return seen


ACCESS = GithubAccess(token=TOKEN)

REPO = {
    "full_name": "acme/billing-api",
    "description": "Invoices and payments",
    "language": "Go",
    "stargazers_count": 12,
    "private": True,
    "pushed_at": "2026-10-09T10:00:00Z",
}


async def test_an_owners_repositories_are_listed_newest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _github(monkeypatch, {"/orgs/acme/repos": [REPO]})

    result = await github_tools.find_repositories(ACCESS, owner="acme")

    assert (
        "acme/billing-api - Invoices and payments (Go, ★12, private; updated 2026-10-09)" in result
    )
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in result


async def test_a_user_owner_falls_back_when_it_is_not_an_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _github(monkeypatch, {"/users/octo/repos": [{**REPO, "full_name": "octo/tool"}]})

    assert "octo/tool" in await github_tools.find_repositories(ACCESS, owner="octo")


async def test_repositories_can_be_searched_within_an_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _github(monkeypatch, {"/search/repositories": {"items": [REPO]}})

    result = await github_tools.find_repositories(ACCESS, query="billing", owner="acme")

    assert "acme/billing-api" in result
    assert "billing user:acme" in str(seen[0].url.params["q"])


async def test_with_no_arguments_the_credentials_own_repositories_are_listed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _github(monkeypatch, {"/user/repos": [REPO]})

    assert "acme/billing-api" in await github_tools.find_repositories(ACCESS)
    assert seen[0].url.path == "/user/repos"


async def test_an_empty_result_says_how_to_continue(monkeypatch: pytest.MonkeyPatch) -> None:
    _github(monkeypatch, {"/search/repositories": {"items": []}})

    assert "No repositories matched" in await github_tools.find_repositories(ACCESS, query="zzz")


async def test_a_file_is_decoded_and_a_directory_is_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    content = base64.b64encode(b"# Billing\nRuns on Go.\n").decode()
    _github(
        monkeypatch,
        {
            "/repos/acme/billing-api/contents/README.md": {
                "type": "file",
                "size": 22,
                "content": content,
            },
            "/repos/acme/billing-api/contents/": [
                {"type": "dir", "path": "cmd"},
                {"type": "file", "path": "go.mod"},
            ],
        },
    )

    readme = await github_tools.read_file(ACCESS, "acme/billing-api", "README.md")
    root = await github_tools.read_file(ACCESS, "https://github.com/acme/billing-api")

    assert "# Billing\nRuns on Go." in readme
    assert "dir   cmd" in root and "file  go.mod" in root


async def test_files_that_are_big_or_binary_or_outside_the_repo_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _github(
        monkeypatch,
        {
            "/repos/a/b/contents/big.sql": {"type": "file", "size": 5_000_000, "content": ""},
            "/repos/a/b/contents/logo.png": {
                "type": "file",
                "size": 4,
                "content": base64.b64encode(b"\x89PNG\xff\xfe").decode(),
            },
        },
    )

    assert "too large" in await github_tools.read_file(ACCESS, "a/b", "big.sql")
    assert "not a text file" in await github_tools.read_file(ACCESS, "a/b", "logo.png")
    assert "must not contain '..'" in await github_tools.read_file(ACCESS, "a/b", "../x")
    assert (await github_tools.read_file(ACCESS, "file:///etc", "x")).startswith("Error:")


async def test_a_missing_repository_explains_visibility_not_just_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _github(monkeypatch, {})

    private = await github_tools.read_file(ACCESS, "acme/secret", "README.md")
    anonymous = await github_tools.read_file(GithubAccess(token=None), "acme/secret", "README.md")

    assert "Not found, or not visible" in private
    assert "no GitHub credential is available" in anonymous


async def test_refusals_say_whether_it_is_the_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _github(monkeypatch, {"/repos/a/b/contents/x": {}}, {"/repos/a/b/contents/x": 403})
    assert "credential is missing, expired or not allowed" in await github_tools.read_file(
        ACCESS, "a/b", "x"
    )
    assert seen

    def limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(limited), **kw),
    )
    assert "rate limit is exhausted" in await github_tools.read_file(ACCESS, "a/b", "x")


async def test_one_issue_shows_its_discussion_and_a_pull_request_its_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue = {
        "number": 12,
        "state": "open",
        "title": "Rounding bug",
        "body": "Totals are off by a cent.",
        "user": {"login": "alice"},
        "labels": [{"name": "bug"}],
        "pull_request": {"url": "x"},
    }
    _github(
        monkeypatch,
        {
            "/repos/acme/billing-api/issues/12": issue,
            "/repos/acme/billing-api/issues/12/comments": [
                {"user": {"login": "bob"}, "body": f"first {n}"} for n in range(8)
            ],
            "/repos/acme/billing-api/pulls/12": {
                "head": {"ref": "fix/rounding"},
                "base": {"ref": "main"},
                "changed_files": 2,
                "additions": 10,
                "deletions": 3,
            },
        },
    )

    result = await github_tools.issues(ACCESS, "acme/billing-api", number=12)

    assert "#12 [open PR] Rounding bug (bug; by alice)" in result
    assert "branch fix/rounding -> main; 2 files, +10 -3" in result
    assert "Totals are off by a cent." in result
    assert result.count("--- bob:") == 5 and "first 7" in result and "first 2" not in result


async def test_issues_can_be_listed_and_searched(monkeypatch: pytest.MonkeyPatch) -> None:
    item = {"number": 3, "state": "open", "title": "Crash", "user": {"login": "c"}, "labels": []}
    seen = _github(
        monkeypatch,
        {"/repos/a/b/issues": [item], "/search/issues": {"items": [item]}},
    )

    assert "#3 [open issue] Crash" in await github_tools.issues(ACCESS, "a/b")
    assert "#3" in await github_tools.issues(ACCESS, "a/b", query="crash")
    assert "repo:a/b crash" in str(seen[-1].url.params["q"])


def _context(**overrides: Any) -> ExecutionContext:
    async def access() -> GithubAccess:
        return ACCESS

    return ExecutionContext(
        session_id="s1",
        event_queue=asyncio.Queue(),
        parent_model="m",
        callbacks=TurnCallbacks(github_access=access),
        **overrides,
    )


async def test_the_agent_sees_the_tools_uses_the_turns_credential_and_may_in_plan_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _github(monkeypatch, {"/orgs/acme/repos": [REPO]})
    registry = ToolRegistry()

    token = current_execution_ctx.set(_context(plan_mode=True))
    try:
        names = {d.name for d in registry.get_tool_definitions()}
        result = await registry.call_tool("github_find_repositories", {"owner": "acme"})
    finally:
        current_execution_ctx.reset(token)

    assert {"github_find_repositories", "github_read_file", "github_issues"} <= names
    assert "acme/billing-api" in result and TOKEN not in result
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"


def test_lookups_need_no_approval_in_any_mode() -> None:
    for mode in ("plan", "ask", "auto_edit", "auto"):
        for tool in ("github_find_repositories", "github_read_file", "github_issues"):
            assert approval_needed(mode, tool, {"repository": "a/b"}) is None


async def test_without_a_turn_credential_only_public_data_is_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _github(monkeypatch, {"/orgs/acme/repos": [REPO]})
    registry = ToolRegistry()

    await registry.call_tool("github_find_repositories", {"owner": "acme"})

    assert "authorization" not in seen[0].headers
