"""Tests for on-demand language diagnostics probe (LSP)."""

from __future__ import annotations

from typing import Any

import pytest
from agent_core.tools.lsp import (
    _BINARY_CACHE,
    DIAGNOSTICS_LABEL,
    SETUP_LABEL,
    run_diagnostics_on_file,
)
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import ExecResult


class _DiagnosticDriver(FakeSandboxDriver):
    def __init__(
        self, installed_bins: set[str], fail_command: bool = False, failure: str = ""
    ) -> None:
        super().__init__()
        self.installed_bins = installed_bins
        self.fail_command = fail_command
        self.failure = failure or "syntax error at line 42: invalid syntax\n"

    async def exec_command(self, session_id: str, command: str, *a: Any, **k: Any) -> ExecResult:
        if command.startswith("which ") or "command -v" in command or "[ -x" in command:
            binary = next((b for b in self.installed_bins if b in command), None)
            if binary:
                return ExecResult(0, f"/usr/bin/{binary}\n", "", 1)
            return ExecResult(1, "", "binary not found\n", 1)

        if self.fail_command:
            return ExecResult(1, "", self.failure, 1)
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


# What `go vet` printed after each edit in session_3116245f7a, whose `make base` had not run.
UNGENERATED = (
    "go: finding module for package github.com/acme/backend-services/generated_pb/history\n"
    "watcher.go:12:2: module github.com/acme/backend-services/generated_pb@latest found "
    "(v0.0.0-00010101000000-000000000000, replaced by ./generated_pb), but does not contain "
    "package github.com/acme/backend-services/generated_pb/history\n"
)


@pytest.mark.asyncio
async def test_a_project_that_is_not_set_up_is_reported_once_and_not_as_a_code_error() -> None:
    import asyncio

    from agent_core.context import ExecutionContext, current_execution_ctx

    _BINARY_CACHE.clear()
    driver = _DiagnosticDriver(installed_bins={"go"}, fail_command=True, failure=UNGENERATED)
    token = current_execution_ctx.set(
        ExecutionContext(session_id="s", event_queue=asyncio.Queue(), parent_model="m")
    )
    try:
        first = await run_diagnostics_on_file(driver, "s", "/workspace/svc/watcher.go")
        second = await run_diagnostics_on_file(driver, "s", "/workspace/svc/config.go")
    finally:
        current_execution_ctx.reset(token)

    assert first is not None and SETUP_LABEL in first and DIAGNOSTICS_LABEL not in first
    assert "does not contain package" in first and "setup" in first
    assert second is None, "said once per turn"


@pytest.mark.asyncio
async def test_a_real_error_keeps_its_label_even_after_a_setup_note() -> None:
    _BINARY_CACHE.clear()
    driver = _DiagnosticDriver(
        installed_bins={"go"}, fail_command=True, failure="watcher.go:40:2: undefined: tokn\n"
    )
    res = await run_diagnostics_on_file(driver, "s2", "/workspace/svc/watcher.go")
    assert res is not None and DIAGNOSTICS_LABEL in res and "undefined: tokn" in res
