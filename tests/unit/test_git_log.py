"""Unit tests for the structured git history tool."""

from __future__ import annotations

from typing import Any

import pytest
from agent_core.tools import tier2

from specifications.interfaces.sandbox import ExecResult

_RECORD_SEP = "\x1e"
_FIELD_SEP = "\x1f"


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


def _record(sha: str, author: str, date: str, subject: str, files: list[str]) -> str:
    footer = "".join(f"{name}\n" for name in files)
    return (
        f"{_RECORD_SEP}{sha}{_FIELD_SEP}{author}{_FIELD_SEP}{date}{_FIELD_SEP}{subject}\n{footer}"
    )


RAW = _record(
    "abcdef1234567890", "Jane Doe", "2026-10-01", "fix: thing", ["foo.py", "bar.py"]
) + _record("deadbeefcafe", "Max Power", "2026-09-30", "feat: other", ["baz.py"])


def test_parse_git_log_extracts_fields_and_files() -> None:
    entries = tier2.parse_git_log(RAW)
    assert [entry["subject"] for entry in entries] == ["fix: thing", "feat: other"]
    assert entries[0]["author"] == "Jane Doe"
    assert entries[0]["date"] == "2026-10-01"
    assert entries[0]["sha"] == "abcdef1234567890"
    assert entries[0]["files"] == ["foo.py", "bar.py"]
    assert entries[1]["files"] == ["baz.py"]


def test_format_git_log_renders_identity_and_files() -> None:
    out = tier2.format_git_log(tier2.parse_git_log(RAW))
    assert "abcdef12 2026-10-01 Jane Doe: fix: thing" in out
    assert "files: foo.py, bar.py" in out


def test_format_git_log_handles_commits_without_files() -> None:
    entry = tier2.parse_git_log(
        f"{_RECORD_SEP}abc{_FIELD_SEP}Jane{_FIELD_SEP}2026-01-01{_FIELD_SEP}chore\n"
    )
    assert "files: (no files changed)" in tier2.format_git_log(entry)


@pytest.mark.asyncio
async def test_git_log_tool_returns_structured_history() -> None:
    driver = FakeDriver([ExecResult(0, RAW, "", 1)])
    out = await tier2.git_log(driver, "s1", max_count=5)
    assert "Jane Doe" in out
    assert "foo.py" in out
    command = driver.commands[0]
    assert "git log" in command
    assert "--date=short" in command
    assert "--name-only" in command
    assert "-n 5" in command


@pytest.mark.asyncio
async def test_git_log_tool_scopes_by_author_and_path() -> None:
    driver = FakeDriver([ExecResult(0, RAW, "", 1)])
    await tier2.git_log(driver, "s1", max_count=3, author="Jane", path="src")
    command = driver.commands[0]
    assert "--author='Jane'" in command
    assert "-- 'src'" in command
    assert "-n 3" in command


@pytest.mark.asyncio
async def test_git_log_tool_reports_non_repo() -> None:
    driver = FakeDriver([ExecResult(0, "Error: workspace is not a git repository", "", 1)])
    out = await tier2.git_log(driver, "s1")
    assert out.startswith("Error:")


@pytest.mark.asyncio
async def test_git_log_tool_handles_empty_history() -> None:
    driver = FakeDriver([ExecResult(0, "", "", 1)])
    assert await tier2.git_log(driver, "s1") == "(no commits)"


def test_registry_exposes_git_log_tool() -> None:
    from agent_core.tools.registry import ToolRegistry

    names = {definition.name for definition in ToolRegistry().get_tool_definitions()}
    assert "git_log" in names
