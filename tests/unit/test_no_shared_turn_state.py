"""Static guard: application code must not bind per-turn state on the shared registry."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNED = [
    ROOT / "apps" / "api" / "src",
    ROOT / "packages" / "slack-assistant" / "src",
    ROOT / "packages" / "agent-core" / "src" / "agent_core" / "orchestrator.py",
    ROOT / "packages" / "agent-core" / "src" / "agent_core" / "subagents.py",
]
FORBIDDEN = {
    "bind_sandbox",
    "set_ensure_sandbox_callback",
    "set_rename_callback",
    "set_kb_search_callback",
    "set_execute_skill_callback",
    "set_question_callback",
    "set_approval_callback",
    "set_diff_callback",
    "set_plan_callback",
    "set_subagent_callback",
}


def _python_files() -> list[Path]:
    files: list[Path] = []
    for entry in SCANNED:
        files.extend([entry] if entry.is_file() else sorted(entry.rglob("*.py")))
    return files


def test_per_turn_collaborators_are_never_installed_on_the_registry() -> None:
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in FORBIDDEN
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.func.attr}")
    assert not offenders, "use TurnCallbacks on the ExecutionContext instead:\n" + "\n".join(
        offenders
    )
