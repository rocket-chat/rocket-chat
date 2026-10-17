"""Tests for on-demand language diagnostics probe (LSP)."""

from __future__ import annotations

from typing import Any

import pytest
from agent_core.tools.lsp import _BINARY_CACHE, run_diagnostics_on_file
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import ExecResult


class _DiagnosticDriver(FakeSandboxDriver):
    def __init__(self, installed_bins: set[str], fail_command: bool = False) -> None:
        super().__init__()
        self.installed_bins = installed_bins
        self.fail_command = fail_command

    async def exec_command(self, session_id: str, command: str, *a: Any, **k: Any) -> ExecResult:
        if command.startswith("which "):
            binary = command.split()[1]
            if binary in self.installed_bins:
                return ExecResult(0, f"/usr/bin/{binary}\n", "", 1)
            return ExecResult(1, "", f"{binary} not found\n", 1)

        if self.fail_command:
            return ExecResult(1, "", "syntax error at line 42: invalid syntax\n", 1)
        return ExecResult(0, "All checks passed\n", "", 1)


@pytest.mark.asyncio
async def test_lsp_diagnostics_silent_when_tool_missing() -> None:
    _BINARY_CACHE.clear()
    driver = _DiagnosticDriver(installed_bins=set())
    res = await run_diagnostics_on_file(driver, "sess_1", "/workspace/main.py")
    assert res is None


@pytest.mark.asyncio
async def test_lsp_diagnostics_silent_on_clean_check() -> None:
    _BINARY_CACHE.clear()
    driver = _DiagnosticDriver(installed_bins={"ruff"}, fail_command=False)
    res = await run_diagnostics_on_file(driver, "sess_2", "/workspace/main.py")
    assert res is None


@pytest.mark.asyncio
async def test_lsp_diagnostics_appends_error_on_failure() -> None:
    _BINARY_CACHE.clear()
    driver = _DiagnosticDriver(installed_bins={"ruff"}, fail_command=True)
    res = await run_diagnostics_on_file(driver, "sess_3", "/workspace/main.py")
    assert res is not None
    assert "[LSP Diagnostics - Syntax/Type Issue]" in res
    assert "syntax error at line 42" in res


@pytest.mark.asyncio
async def test_lsp_diagnostics_skips_unsupported_extension() -> None:
    _BINARY_CACHE.clear()
    driver = _DiagnosticDriver(installed_bins={"ruff"}, fail_command=True)
    res = await run_diagnostics_on_file(driver, "sess_4", "/workspace/readme.txt")
    assert res is None
