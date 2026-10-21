"""Tests for multi-repository workspace manifest and cross-repo operations (C2 & C3)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.tools.builtin.workspace import (
    _clamp_output,
    _detect_repo_dependencies,
    _filter_repos,
    _topological_sort,
)
from agent_core.tools.registry import ToolRegistry
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.agent import TurnCallbacks, TurnPolicy
from specifications.interfaces.sandbox import ExecResult


class _MultiRepoDriver(FakeSandboxDriver):
    def __init__(self, repos: list[str]) -> None:
        super().__init__()
        self.repo_list = repos
        self.commands: list[tuple[str, str | None]] = []
        self.files: dict[str, str] = {}
        self.exec_responses: dict[str, ExecResult] = {}
        self.branches: dict[str, str] = {}

    async def exec_command(
        self,
        session_id: str,
        command: str,
        *,
        workdir: str | None = None,
        timeout_seconds: int = 60,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        self.commands.append((command, workdir))
        # Custom mock responses
        for pattern, res in self.exec_responses.items():
            if pattern in command:
                return res

        if "find " in command:
            import posixpath

            # list_repositories probe expects @<relpath>
            output = "\n".join(f"@{posixpath.basename(r)}" for r in self.repo_list)
            return ExecResult(0, output, "", 1)

        if "git -C" in command and "rev-parse --abbrev-ref HEAD" in command:
            # The batched probe: one "path<TAB>branch" line per repository.
            import re

            paths = re.findall(r"printf '%s\\t' (\S+);", command)
            branches = {p: self.branches.get(p, "main") for p in paths}
            return ExecResult(0, "".join(f"{p}\t{b}\n" for p, b in branches.items()), "", 1)

        if "git rev-parse --abbrev-ref HEAD" in command:
            return ExecResult(0, "main\n", "", 1)

        if "git log -1" in command:
            return ExecResult(0, "a1b2c3d Initial commit\n", "", 1)

        if "git status --porcelain -b" in command:
            if workdir and "dirty" in workdir:
                return ExecResult(0, "## main\n M src/app.py\n?? untracked.txt\n", "", 1)
            return ExecResult(0, "## main\n", "", 1)

        if "git status --porcelain" in command:
            if workdir and "dirty" in workdir:
                return ExecResult(0, " M src/app.py\n?? untracked.txt\n", "", 1)
            return ExecResult(0, "", "", 1)

        if "cat << 'EOF' > /workspace/.rocket/workspace.json" in command:
            content = command.split("cat << 'EOF' > /workspace/.rocket/workspace.json\n", 1)[1]
            content = content.rsplit("\nEOF", 1)[0]
            self.files["/workspace/.rocket/workspace.json"] = content
            return ExecResult(0, "", "", 1)

        if "mkdir -p /workspace/.rocket" in command:
            return ExecResult(0, "", "", 1)

        return ExecResult(0, "ok\n", "", 1)


def _setup_context(session_id: str = "sess-1") -> None:
    current_execution_ctx.set(
        ExecutionContext(
            session_id=session_id,
            event_queue=asyncio.Queue(),
            parent_model="test-model",
            tenant_org_id="org-test",
            policy=TurnPolicy(permission_mode="auto"),
            callbacks=TurnCallbacks(),
        )
    )


def test_topological_sort_independent() -> None:
    dep_map = {"repo-a": [], "repo-b": [], "repo-c": []}
    order = _topological_sort(dep_map)
    assert set(order) == {"repo-a", "repo-b", "repo-c"}


def test_topological_sort_linear_and_diamond() -> None:
    # A depends on B, B depends on C -> C must precede B, B must precede A
    dep_map = {
        "service-a": ["lib-b"],
        "lib-b": ["core-c"],
        "core-c": [],
    }
    order = _topological_sort(dep_map)
    assert order.index("core-c") < order.index("lib-b")
    assert order.index("lib-b") < order.index("service-a")

    # Diamond dependency: web depends on api and shared; api depends on shared
    diamond = {
        "web": ["api", "shared"],
        "api": ["shared"],
        "shared": [],
    }
    diamond_order = _topological_sort(diamond)
    assert diamond_order.index("shared") < diamond_order.index("api")
    assert diamond_order.index("api") < diamond_order.index("web")


def test_topological_sort_handles_cycles() -> None:
    cycle = {
        "repo-x": ["repo-y"],
        "repo-y": ["repo-x"],
        "repo-z": [],
    }
    # Should not infinite loop and should return all nodes
    order = _topological_sort(cycle)
    assert set(order) == {"repo-x", "repo-y", "repo-z"}


def test_filter_repos() -> None:
    all_repos = ["/workspace/api", "/workspace/web", "/workspace/worker"]
    assert _filter_repos(all_repos, None) == all_repos
    assert _filter_repos(all_repos, "") == all_repos
    assert _filter_repos(all_repos, "api, worker") == ["/workspace/api", "/workspace/worker"]
    assert _filter_repos(all_repos, "nonexistent") == []


def test_clamp_output() -> None:
    short_out = "line 1\nline 2"
    assert _clamp_output(short_out, None) == "line 1\nline 2"
    assert _clamp_output(None, None) == "(no output)"

    long_out = "\n".join(f"line {i}" for i in range(50))
    clamped = _clamp_output(long_out, None)
    assert "... [30 lines omitted] ..." in clamped
    assert "line 0" in clamped
    assert "line 49" in clamped


async def test_detect_repo_dependencies() -> None:
    driver = FakeSandboxDriver()
    session_id = "s1"
    driver.exec_command = lambda *args, **kwargs: pytest.fail("unexpected")  # type: ignore

    async def mock_exec(s: str, cmd: str, **kwargs: Any) -> ExecResult:
        manifest_data = """{
            "dependencies": {
                "shared-lib": "^1.0.0",
                "auth-lib": "^2.0.0"
            }
        }"""
        return ExecResult(0, manifest_data, "", 1)

    driver.exec_command = mock_exec  # type: ignore
    deps = await _detect_repo_dependencies(
        driver, session_id, "/workspace/web-app", ["web-app", "shared-lib", "auth-lib", "other-lib"]
    )
    assert deps == ["auth-lib", "shared-lib"]


async def test_workspace_status_15_repos() -> None:
    _setup_context("sess-15")
    repos = [f"/workspace/repo-{i:02d}" for i in range(15)]
    # Mark repo-05 as dirty
    repos[5] = "/workspace/repo-05-dirty"

    driver = _MultiRepoDriver(repos)
    registry = ToolRegistry(driver=driver)

    result = await registry.call_tool("workspace_status", {})
    assert "| Repository | Branch | Head Commit | Status | Dependencies |" in result
    assert "repo-00" in result
    assert "repo-14" in result
    assert "Dirty" in result
    assert "Clean" in result

    # Check that manifest file was written
    assert "/workspace/.rocket/workspace.json" in driver.files
    manifest = json.loads(driver.files["/workspace/.rocket/workspace.json"])
    assert manifest["version"] == 1
    assert len(manifest["repositories"]) == 15
    assert len(manifest["dependency_order"]) == 15


async def test_git_status_all() -> None:
    _setup_context("sess-status")
    repos = ["/workspace/clean-repo", "/workspace/dirty-repo"]
    driver = _MultiRepoDriver(repos)
    registry = ToolRegistry(driver=driver)

    res_all = await registry.call_tool("git_status_all", {"only_dirty": False})
    assert "[clean-repo] branch: main (clean)" in res_all
    assert "[dirty-repo] branch: main (2 changed files)" in res_all
    assert "M src/app.py" in res_all

    res_dirty = await registry.call_tool("git_status_all", {"only_dirty": True})
    assert "clean-repo" not in res_dirty
    assert "[dirty-repo] branch: main (2 changed files)" in res_dirty


async def test_repo_foreach() -> None:
    _setup_context("sess-foreach")
    repos = ["/workspace/pkg-a", "/workspace/pkg-b", "/workspace/pkg-c"]
    driver = _MultiRepoDriver(repos)
    registry = ToolRegistry(driver=driver)

    res = await registry.call_tool("repo_foreach", {"command": "echo test"})
    assert "=== pkg-a (exit 0) ===" in res
    assert "=== pkg-b (exit 0) ===" in res
    assert "=== pkg-c (exit 0) ===" in res
    assert "Summary: 3 passed, 0 failed out of 3 targeted." in res

    # Test filtering
    res_filter = await registry.call_tool(
        "repo_foreach", {"command": "echo test", "repos": "pkg-a,pkg-c"}
    )
    assert "pkg-a" in res_filter
    assert "pkg-c" in res_filter
    assert "pkg-b" not in res_filter
    assert "Summary: 2 passed, 0 failed out of 2 targeted." in res_filter

    # Test fail-fast
    async def failing_exec(
        session_id: str, command: str, *, workdir: str | None = None, **kwargs: Any
    ) -> ExecResult:
        if "find " in command:
            import posixpath

            output = "\n".join(f"@{posixpath.basename(r)}" for r in repos)
            return ExecResult(0, output, "", 1)
        if workdir == "/workspace/pkg-a":
            return ExecResult(1, "", "build failed", 1)
        return ExecResult(0, "ok", "", 1)

    driver.exec_command = failing_exec  # type: ignore
    res_fail = await registry.call_tool("repo_foreach", {"command": "make test", "fail_fast": True})
    assert "Execution halted early due to failure in pkg-a (fail_fast=True)." in res_fail
    assert "pkg-b" not in res_fail


async def test_build_workspace_context() -> None:
    _setup_context("sess-ctx")

    # Single repo: returns empty string
    driver_single = _MultiRepoDriver(["/workspace/single"])
    registry_single = ToolRegistry(driver=driver_single)
    ctx_single = await registry_single.build_workspace_context()
    assert ctx_single == ""

    # Multi repos: returns <= 10 lines summary
    repos = [f"/workspace/repo-{i:02d}" for i in range(12)]
    driver_multi = _MultiRepoDriver(repos)
    # The agent switched one repository to its own branch: that is what the note must say.
    driver_multi.branches["/workspace/repo-01"] = "feat/rocket-SVC-21926"
    registry_multi = ToolRegistry(driver=driver_multi)
    ctx_multi = await registry_multi.build_workspace_context()
    lines = ctx_multi.splitlines()
    assert len(lines) <= 10
    assert "Workspace Repositories (12):" in lines[0]
    assert "- repo-00 (branch: main)" in ctx_multi
    assert "- repo-01 (branch: feat/rocket-SVC-21926)" in ctx_multi
    assert "... and 4 more repositories" in ctx_multi
