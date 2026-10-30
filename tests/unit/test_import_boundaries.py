"""Package boundaries are enforced, not just agreed on.

* Applications may import packages; packages never import an application (``api``).
* ``specifications`` (the shared contracts) imports no workspace package at all.
* The package dependency graph has no cycles.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PACKAGES = {
    "agent_core": "packages/agent-core/src/agent_core",
    "chat_bridges": "packages/chat-bridges/src/chat_bridges",
    "config_engine": "packages/config-engine/src/config_engine",
    "git_engine": "packages/git-engine/src/git_engine",
    "llm_gateway": "packages/llm-gateway/src/llm_gateway",
    "mcp_runner": "packages/mcp-runner/src/mcp_runner",
    "sandbox_driver": "packages/sandbox-driver/src/sandbox_driver",
    "slack_assistant": "packages/slack-assistant/src/slack_assistant",
}
CONTRACTS = {"specifications": "specifications"}
APPLICATIONS = {"api": "apps/api/src/api"}


def _imported_roots(directory: str) -> set[str]:
    roots: set[str] = set()
    for source in (ROOT / directory).rglob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _graph() -> dict[str, set[str]]:
    known = {*PACKAGES, *CONTRACTS, *APPLICATIONS}
    return {
        name: _imported_roots(directory) & known - {name}
        for name, directory in {**PACKAGES, **CONTRACTS}.items()
    }


def test_no_package_imports_an_application() -> None:
    offenders = {
        name: sorted(deps & set(APPLICATIONS))
        for name, deps in _graph().items()
        if deps & set(APPLICATIONS)
    }
    assert not offenders, f"packages must not import applications: {offenders}"


def test_the_shared_contracts_import_no_workspace_package() -> None:
    assert _graph()["specifications"] == set()


def test_the_package_dependency_graph_has_no_cycles() -> None:
    graph = _graph()
    state: dict[str, str] = {}
    path: list[str] = []

    def visit(name: str) -> list[str] | None:
        if state.get(name) == "done":
            return None
        if state.get(name) == "visiting":
            return [*path[path.index(name) :], name]
        state[name] = "visiting"
        path.append(name)
        for dependency in sorted(graph.get(name, ())):
            if cycle := visit(dependency):
                return cycle
        path.pop()
        state[name] = "done"
        return None

    for node in sorted(graph):
        cycle = visit(node)
        assert cycle is None, f"import cycle between packages: {' -> '.join(cycle or [])}"
