"""Multi-repository workspace management and cross-repo operations (Items C2 & C3)."""

from __future__ import annotations

import json
import logging
import posixpath
import time
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field
from sandbox_driver.workspace import WORKSPACE, list_repositories

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

MANIFEST_PATH = "/workspace/.rocket/workspace.json"
MAX_LINES_PER_REPO = 20


def register(registry: ToolRegistry) -> None:
    _register_workspace_status(registry)
    _register_git_status_all(registry)
    _register_repo_foreach(registry)


def _register_workspace_status(registry: ToolRegistry) -> None:
    @registry._tool(
        description=(
            "Inspect all repositories in the workspace, detect inter-repo dependencies, "
            "write/update /workspace/.rocket/workspace.json, and return a structured summary table."
        )
    )
    async def workspace_status(
        refresh: Annotated[
            bool,
            Field(description="Whether to force re-scanning every repository from disk."),
        ] = False,
        scan_dependencies: Annotated[
            bool,
            Field(
                description="Whether to scan package manifests for inter-repository dependencies."
            ),
        ] = True,
    ) -> str:
        target = registry._sandbox_target("workspace_status")
        if not target:
            return "Error: Sandbox not initialized for workspace_status"
        _, session_id = target
        await registry._ensure_sandbox(session_id)
        driver = registry._driver
        assert driver is not None

        repos = await list_repositories(driver, session_id)
        if not repos:
            return "No git repositories found under /workspace."

        repo_info_list: list[dict[str, Any]] = []
        repo_names = [posixpath.basename(r) for r in repos]

        for repo_dir in repos:
            name = posixpath.basename(repo_dir)
            info = await _inspect_single_repo(driver, session_id, repo_dir, name)
            if scan_dependencies:
                deps = await _detect_repo_dependencies(driver, session_id, repo_dir, repo_names)
                info["dependencies"] = deps
            else:
                info["dependencies"] = []
            repo_info_list.append(info)

        dep_map = {r["name"]: r["dependencies"] for r in repo_info_list}
        order = _topological_sort(dep_map)

        manifest = {
            "version": 1,
            "updated_at": int(time.time()),
            "repositories": repo_info_list,
            "dependency_order": order,
        }
        await _save_manifest(driver, session_id, manifest)

        return _format_status_table(repo_info_list, order)


def _register_git_status_all(registry: ToolRegistry) -> None:
    @registry._tool(
        description=(
            "Return porcelain git status across all repositories in the workspace, "
            "highlighting branches and dirty worktrees."
        )
    )
    async def git_status_all(
        only_dirty: Annotated[
            bool,
            Field(
                description="If True, only show repositories with uncommitted changes or untracked files."
            ),
        ] = False,
    ) -> str:
        target = registry._sandbox_target("git_status_all")
        if not target:
            return "Error: Sandbox not initialized for git_status_all"
        _, session_id = target
        await registry._ensure_sandbox(session_id)
        driver = registry._driver
        assert driver is not None

        repos = await list_repositories(driver, session_id)
        if not repos:
            return "No git repositories found under /workspace."

        lines: list[str] = []
        for repo_dir in repos:
            name = posixpath.basename(repo_dir)
            res = await driver.exec_command(
                session_id, "git status --porcelain -b", workdir=repo_dir, timeout_seconds=20
            )
            raw = (res.stdout or "").strip()
            status_lines = raw.splitlines() if raw else []
            branch = status_lines[0].replace("## ", "") if status_lines else "unknown"
            changes = status_lines[1:] if len(status_lines) > 1 else []
            is_dirty = len(changes) > 0

            if only_dirty and not is_dirty:
                continue

            status_str = f"{len(changes)} changed files" if is_dirty else "clean"
            lines.append(f"[{name}] branch: {branch} ({status_str})")
            if is_dirty:
                for c in changes[:10]:
                    lines.append(f"  {c}")
                if len(changes) > 10:
                    lines.append(f"  ... and {len(changes) - 10} more")

        return "\n".join(lines) if lines else "All repositories are clean."


def _register_repo_foreach(registry: ToolRegistry) -> None:
    @registry._tool(
        description=(
            "Execute a command across every repository in the workspace (or a subset), "
            "aggregating clamped output and status codes per repository."
        )
    )
    async def repo_foreach(
        command: Annotated[str, Field(description="Shell command to run in each repository.")],
        repos: Annotated[
            str | None,
            Field(description="Comma-separated repository names to target (default: all)."),
        ] = None,
        fail_fast: Annotated[
            bool,
            Field(description="Stop executing if any repository command returns non-zero."),
        ] = False,
    ) -> str:
        target = registry._sandbox_target("repo_foreach")
        if not target:
            return "Error: Sandbox not initialized for repo_foreach"
        _, session_id = target
        await registry._ensure_sandbox(session_id)
        driver = registry._driver
        assert driver is not None

        all_repos = await list_repositories(driver, session_id)
        if not all_repos:
            return "No git repositories found under /workspace."

        filtered = _filter_repos(all_repos, repos)
        if not filtered:
            return f"No matching repositories found for filter: {repos}"

        cmd_lower = command.strip().lower()
        if (
            any(
                cmd_lower.startswith(prefix)
                for prefix in ("git commit", "git push", "git reset", "git merge", "git rebase")
            )
            or "rm -rf" in cmd_lower
        ):
            return (
                "Error: `repo_foreach` is restricted to read-only or test/verification commands. "
                "Use individual tools or targeted bash_exec for repository edits."
            )

        outputs: list[str] = []
        passed = 0
        failed = 0

        for repo_dir in filtered:
            name = posixpath.basename(repo_dir)
            res = await driver.exec_command(
                session_id, command, workdir=repo_dir, timeout_seconds=60
            )
            out = _clamp_output(res.stdout, res.stderr)
            outputs.append(f"=== {name} (exit {res.exit_code}) ===\n{out}")
            if res.exit_code == 0:
                passed += 1
            else:
                failed += 1
                if fail_fast:
                    outputs.append(
                        f"Execution halted early due to failure in {name} (fail_fast=True)."
                    )
                    break

        summary = f"Summary: {passed} passed, {failed} failed out of {len(filtered)} targeted."
        return f"{chr(10).join(outputs)}\n\n{summary}"


async def _inspect_single_repo(
    driver: Any, session_id: str, repo_dir: str, name: str
) -> dict[str, Any]:
    branch_res = await driver.exec_command(
        session_id, "git rev-parse --abbrev-ref HEAD", workdir=repo_dir, timeout_seconds=15
    )
    branch = (branch_res.stdout or "").strip() or "HEAD"

    log_res = await driver.exec_command(
        session_id, "git log -1 --format='%h %s'", workdir=repo_dir, timeout_seconds=15
    )
    head_commit = (log_res.stdout or "").strip() or "No commits"

    status_res = await driver.exec_command(
        session_id, "git status --porcelain", workdir=repo_dir, timeout_seconds=15
    )
    status_raw = (status_res.stdout or "").strip()
    status_lines = status_raw.splitlines() if status_raw else []
    modified = [line[3:].strip() for line in status_lines if not line.startswith("??")]
    untracked = [line[3:].strip() for line in status_lines if line.startswith("??")]

    return {
        "name": name,
        "path": repo_dir,
        "branch": branch,
        "head_commit": head_commit,
        "is_dirty": len(status_lines) > 0,
        "modified_files": modified,
        "untracked_files": untracked,
    }


async def _detect_repo_dependencies(
    driver: Any, session_id: str, repo_dir: str, all_repo_names: list[str]
) -> list[str]:
    """Scan manifests in repo_dir to see if other repositories are referenced as dependencies."""
    deps: set[str] = set()
    other_names = [n for n in all_repo_names if n != posixpath.basename(repo_dir)]
    if not other_names:
        return []

    probe_cmd = (
        "cat package.json pyproject.toml go.mod Cargo.toml requirements.txt 2>/dev/null || true"
    )
    res = await driver.exec_command(session_id, probe_cmd, workdir=repo_dir, timeout_seconds=15)
    content = res.stdout or ""
    for name in other_names:
        if name in content:
            deps.add(name)

    return sorted(deps)


def _topological_sort(dep_map: dict[str, list[str]]) -> list[str]:
    """Sort repos in dependency order (dependencies before dependents)."""
    visited: set[str] = set()
    temp: set[str] = set()
    order: list[str] = []

    def visit(node: str) -> None:
        if node in temp:
            return
        if node not in visited:
            temp.add(node)
            for dep in dep_map.get(node, []):
                if dep in dep_map:
                    visit(dep)
            temp.remove(node)
            visited.add(node)
            order.append(node)

    for item in sorted(dep_map.keys()):
        if item not in visited:
            visit(item)

    return order


async def sync_workspace_manifest(
    driver: Any, session_id: str, active_repo: str | None = None
) -> None:
    """Scan repositories and update /workspace/.rocket/workspace.json."""
    repos = await list_repositories(driver, session_id)
    if not repos:
        return
    repo_names = [posixpath.basename(r) for r in repos]
    repo_info_list: list[dict[str, Any]] = []
    for repo_dir in repos:
        name = posixpath.basename(repo_dir)
        info = await _inspect_single_repo(driver, session_id, repo_dir, name)
        info["dependencies"] = await _detect_repo_dependencies(
            driver, session_id, repo_dir, repo_names
        )
        if active_repo and (repo_dir == active_repo or name == posixpath.basename(active_repo)):
            info["is_active"] = True
        else:
            info["is_active"] = False
        repo_info_list.append(info)

    dep_map = {r["name"]: r["dependencies"] for r in repo_info_list}
    order = _topological_sort(dep_map)
    manifest = {
        "version": 1,
        "updated_at": int(time.time()),
        "active_repository": posixpath.basename(active_repo) if active_repo else None,
        "repositories": repo_info_list,
        "dependency_order": order,
    }
    await _save_manifest(driver, session_id, manifest)


async def _save_manifest(driver: Any, session_id: str, manifest: dict[str, Any]) -> None:
    content = json.dumps(manifest, indent=2)
    mkdir_cmd = "mkdir -p /workspace/.rocket"
    await driver.exec_command(session_id, mkdir_cmd, workdir=WORKSPACE, timeout_seconds=15)
    write_cmd = f"cat << 'EOF' > {MANIFEST_PATH}\n{content}\nEOF"
    await driver.exec_command(session_id, write_cmd, workdir=WORKSPACE, timeout_seconds=15)


def _format_status_table(repos: list[dict[str, Any]], order: list[str]) -> str:
    rows = [
        "| Repository | Branch | Head Commit | Status | Dependencies |",
        "|---|---|---|---|---|",
    ]
    for r in repos:
        name = r["name"]
        branch = r["branch"]
        head = r["head_commit"][:30]
        status = (
            "Clean"
            if not r["is_dirty"]
            else f"Dirty ({len(r['modified_files'])} mod, {len(r['untracked_files'])} untr)"
        )
        deps = ", ".join(r["dependencies"]) if r["dependencies"] else "None"
        rows.append(f"| {name} | {branch} | {head} | {status} | {deps} |")

    order_str = " -> ".join(order) if order else "None"
    return f"{chr(10).join(rows)}\n\n**Dependency Order:** {order_str}"


def _filter_repos(all_repos: list[str], target_filter: str | None) -> list[str]:
    if not target_filter or not target_filter.strip():
        return all_repos
    wanted = {p.strip() for p in target_filter.split(",") if p.strip()}
    return [r for r in all_repos if posixpath.basename(r) in wanted or r in wanted]


def _clamp_output(stdout: str | None, stderr: str | None) -> str:
    combined = (stdout or "") + ("\n" + stderr if stderr else "")
    lines = combined.strip().splitlines()
    if len(lines) <= MAX_LINES_PER_REPO:
        return combined.strip() or "(no output)"
    head = lines[:14]
    tail = lines[-6:]
    omitted = len(lines) - 20
    return "\n".join([*head, f"... [{omitted} lines omitted] ...", *tail])
