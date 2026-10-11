"""Every setting an administrator or member can change has a test that proves it does something.

A setting that is declared, validated and shown in the settings pages but never changes behaviour
is worse than no setting: people configure it and believe it. A behaviour test therefore carries
``@pytest.mark.setting("domain.key")``; this module fails when a registered setting has none, or
when a marker names a setting that no longer exists.
"""

from __future__ import annotations

import ast
from pathlib import Path

from config_engine.registry import REGISTRY

TESTS = Path(__file__).resolve().parents[1]


def _marked_settings() -> dict[str, list[str]]:
    marked: dict[str, list[str]] = {}
    for path in TESTS.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and ast.unparse(decorator.func) == "pytest.mark.setting"
                    and decorator.args
                    and isinstance(decorator.args[0], ast.Constant)
                ):
                    marked.setdefault(str(decorator.args[0].value), []).append(
                        f"{path.relative_to(TESTS)}::{node.name}"
                    )
    return marked


def test_every_registered_setting_has_a_behaviour_test() -> None:
    marked = _marked_settings()
    missing = [
        f"{domain}.{key}"
        for domain, specs in REGISTRY.items()
        for key in specs
        if f"{domain}.{key}" not in marked
    ]
    assert not missing, (
        f"{len(missing)} settings have no test marked @pytest.mark.setting(...): "
        + ", ".join(missing)
    )


def test_markers_only_name_settings_that_exist() -> None:
    known = {f"{domain}.{key}" for domain, specs in REGISTRY.items() for key in specs}
    unknown = sorted(set(_marked_settings()) - known)
    assert not unknown, f"markers for unknown settings: {unknown}"
