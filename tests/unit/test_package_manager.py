"""Unit tests for the package-manager abstraction."""

from __future__ import annotations

from typing import Any

import pytest
from agent_core.tools import package_manager

from specifications.interfaces.sandbox import ExecResult


class FakeDriver:
    """Minimal sandbox driver stand-in capturing commands and replaying results."""

    def __init__(self, results: list[ExecResult] | None = None) -> None:
        self.commands: list[str] = []
        self._results = list(results or [])

    async def exec_command(self, session_id: str, command: str, **_: Any) -> ExecResult:
        self.commands.append(command)
        if self._results:
            return self._results.pop(0)
        return ExecResult(exit_code=0, stdout="", stderr="", duration_ms=1)


def _probe(files: tuple[str, ...] = (), package_json: str = "", pyproject: str = "") -> ExecResult:
    lines = [f"__ROCKET_FILE__:{name}" for name in files]
    lines.append("__ROCKET_PKGJSON__")
    if package_json:
        lines.extend(package_json.splitlines())
    lines.append("__ROCKET_PYPROJECT__")
    if pyproject:
        lines.extend(pyproject.splitlines())
    return ExecResult(exit_code=0, stdout="\n".join(lines) + "\n", stderr="", duration_ms=1)


def test_candidates_cover_spec_managers_in_priority_order() -> None:
    labels = [pm.label for pm in package_manager.candidates()]
    assert labels == ["pnpm", "npm", "poetry", "uv"]


def test_detect_manager_prefers_declared_lockfiles() -> None:
    assert package_manager.detect_manager({"pnpm-lock.yaml", "package.json"}) == "pnpm"
    assert package_manager.detect_manager({"package-lock.json"}) == "npm"
    assert package_manager.detect_manager({"npm-shrinkwrap.json"}) == "npm"
    assert package_manager.detect_manager({"poetry.lock"}) == "poetry"
    assert package_manager.detect_manager({"uv.lock"}) == "uv"


def test_detect_manager_uses_package_manager_field() -> None:
    assert (
        package_manager.detect_manager({"package.json"}, '{"packageManager": "pnpm@9.0.0"}')
        == "pnpm"
    )
    assert (
        package_manager.detect_manager({"package.json"}, '{"packageManager": "npm@10.0.0"}')
        == "npm"
    )


def test_detect_manager_python_fallbacks() -> None:
    assert package_manager.detect_manager({"pyproject.toml"}) == "uv"
    assert (
        package_manager.detect_manager({"pyproject.toml"}, pyproject_text="[tool.poetry]\nname='x'")
        == "poetry"
    )
    assert package_manager.detect_manager({"pyproject.toml"}, pyproject_text="[tool.uv]\n") == "uv"


def test_detect_manager_returns_none_for_empty_workspace() -> None:
    assert package_manager.detect_manager(set()) is None


def test_parse_failure_extracts_actionable_line() -> None:
    assert "npm ERR!" in package_manager.parse_failure("noise\nnpm ERR! code E404\nmore")
    assert "ERR_PNPM_" in package_manager.parse_failure("ERR_PNPM_OUTDATED_LOCKFILE boom")
    assert package_manager.parse_failure("everything is fine") == ""


@pytest.mark.asyncio
async def test_install_is_lockfile_aware_for_pnpm() -> None:
    driver = FakeDriver([_probe(("pnpm-lock.yaml", "package.json")), ExecResult(0, "Done", "", 1)])
    out = await package_manager.package_manager(driver, "s1", action="install")
    assert "[package_manager/pnpm] INSTALL PASSED" in out
    assert "pnpm install --frozen-lockfile" in driver.commands[1]


@pytest.mark.asyncio
async def test_install_uses_npm_ci_when_lockfile_present() -> None:
    driver = FakeDriver(
        [_probe(("package-lock.json", "package.json")), ExecResult(0, "added 1 package", "", 1)]
    )
    await package_manager.package_manager(driver, "s1", action="install")
    assert "npm ci" in driver.commands[1]


@pytest.mark.asyncio
async def test_add_preserves_package_order_and_dev_flag() -> None:
    driver = FakeDriver(
        [_probe(("package-lock.json", "package.json")), ExecResult(0, "added 2 packages", "", 1)]
    )
    await package_manager.package_manager(
        driver, "s1", action="add", packages=["left-pad", "right-pad"], dev=True
    )
    command = driver.commands[1]
    assert "npm install" in command
    assert "--save-dev" in command
    assert command.index("left-pad") < command.index("right-pad")


@pytest.mark.asyncio
async def test_parsed_failure_surfaces_cause() -> None:
    stderr = "ERR_PNPM_NO_MATCHING_VERSION No matching version found for left-pad@9.9.9"
    driver = FakeDriver([_probe(("pnpm-lock.yaml", "package.json")), ExecResult(1, "", stderr, 1)])
    out = await package_manager.package_manager(driver, "s1", action="add", packages=["left-pad"])
    assert "FAILED (exit 1)" in out
    assert "ERR_PNPM_NO_MATCHING_VERSION" in out


@pytest.mark.asyncio
async def test_add_requires_packages_without_running_command() -> None:
    driver = FakeDriver([_probe(("package-lock.json", "package.json"))])
    out = await package_manager.package_manager(driver, "s1", action="add", packages=[])
    assert "requires a non-empty" in out
    assert driver.commands == []


@pytest.mark.asyncio
async def test_unknown_action_is_rejected() -> None:
    driver = FakeDriver()
    out = await package_manager.package_manager(driver, "s1", action="dance")
    assert "unsupported" in out
    assert driver.commands == []


@pytest.mark.asyncio
async def test_no_workspace_declaration_is_reported() -> None:
    driver = FakeDriver([_probe()])
    out = await package_manager.package_manager(driver, "s1")
    assert "no uv/pnpm/npm/poetry project detected" in out


@pytest.mark.asyncio
async def test_explicit_manager_override_skips_detection() -> None:
    driver = FakeDriver([_probe(), ExecResult(0, "synced", "", 1)])
    out = await package_manager.package_manager(driver, "s1", action="install", manager="uv")
    assert "[package_manager/uv]" in out
    assert "uv sync" in driver.commands[1]


def test_registry_exposes_package_manager_tool() -> None:
    from agent_core.tools.registry import ToolRegistry

    names = {definition.name for definition in ToolRegistry().get_tool_definitions()}
    assert "package_manager" in names


@pytest.mark.asyncio
async def test_registry_dispatches_package_manager() -> None:
    from agent_core.tools.registry import ToolRegistry

    driver = FakeDriver([_probe(("package-lock.json", "package.json")), ExecResult(0, "ok", "", 1)])
    registry = ToolRegistry(driver=driver, session_id="s1")
    out = await registry.call_tool("package_manager", {"action": "install"})
    assert "[package_manager/npm]" in out
