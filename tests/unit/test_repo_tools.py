"""Unit tests for Tier 2 repository-intelligence and verification tools."""

from __future__ import annotations

from typing import Any

import pytest
from agent_core.tools import tier2

from specifications.interfaces.sandbox import ExecResult


class FakeDriver:
    """Minimal SandboxDriverProtocol stand-in capturing commands and replaying results."""

    def __init__(self, results: list[ExecResult] | None = None) -> None:
        self.commands: list[str] = []
        self._results = list(results or [])

    async def exec_command(
        self,
        session_id: str,
        command: str,
        workdir: str = "/workspace",
        timeout_seconds: int = 120,
        env: dict[str, str] | None = None,
        on_stdout_chunk: Any = None,
    ) -> ExecResult:
        self.commands.append(command)
        if self._results:
            return self._results.pop(0)
        return ExecResult(exit_code=0, stdout="", stderr="", duration_ms=1)


def test_sh_quote_escapes_single_quotes() -> None:
    assert tier2._sh_quote("plain") == "'plain'"
    assert tier2._sh_quote("it's") == "'it'\\''s'"


def test_glob_to_regex_supports_recursion() -> None:
    assert tier2._glob_to_regex("*.py").endswith("[^/]*\\.py$")
    regex = tier2._glob_to_regex("src/**/*.py")
    assert regex.startswith("(^|/)")
    assert ".*" in regex
    assert regex.endswith("\\.py$")


@pytest.mark.asyncio
async def test_grep_search_uses_ripgrep_with_grep_fallback() -> None:
    driver = FakeDriver([ExecResult(0, "src/a.py:3:def foo():\n", "", 1)])
    out = await tier2.grep_search(driver, "s1", "def foo", path="src", file_glob="*.py")
    assert "src/a.py:3:def foo():" in out
    command = driver.commands[0]
    assert "rg " in command
    assert "grep " in command
    assert "--include='*.py'" in command
    assert "-g '*.py'" in command


@pytest.mark.asyncio
async def test_grep_search_reports_no_matches() -> None:
    driver = FakeDriver([ExecResult(1, "", "", 1)])
    out = await tier2.grep_search(driver, "s1", "nope")
    assert "No matches found" in out


@pytest.mark.asyncio
async def test_grep_search_case_insensitive_flag() -> None:
    driver = FakeDriver([ExecResult(0, "x", "", 1)])
    await tier2.grep_search(driver, "s1", "abc", case_sensitive=False)
    assert "--ignore-case" in driver.commands[0]
    assert " -i " in driver.commands[0]


@pytest.mark.asyncio
async def test_glob_files_returns_matches_and_handles_empty() -> None:
    driver = FakeDriver([ExecResult(0, "src/a.py\nsrc/b.py\n", "", 1), ExecResult(0, "", "", 1)])
    out = await tier2.glob_files(driver, "s1", "src/**/*.py")
    assert "src/a.py" in out
    out_empty = await tier2.glob_files(driver, "s1", "none/*.rs")
    assert "No files matched" in out_empty


@pytest.mark.asyncio
async def test_list_dir_formats_output() -> None:
    driver = FakeDriver([ExecResult(0, "total 1\n-rw-r--r-- 1 root root 3 f.py\n", "", 1)])
    out = await tier2.list_dir(driver, "s1", ".")
    assert "f.py" in out
    assert driver.commands[0].startswith("ls -la")


@pytest.mark.asyncio
async def test_git_status_and_diff() -> None:
    driver = FakeDriver(
        [
            ExecResult(0, "## main\n M file.py\n", "", 1),
            ExecResult(0, "", "", 1),
        ]
    )
    status = await tier2.git_status(driver, "s1")
    assert "## main" in status
    diff = await tier2.git_diff(driver, "s1")
    assert diff == "(no differences)"
    assert "git diff" in driver.commands[1]


@pytest.mark.asyncio
async def test_git_diff_staged_scope_and_stat() -> None:
    driver = FakeDriver([ExecResult(0, " 1 file changed", "", 1)])
    await tier2.git_diff(driver, "s1", path="a.py", staged=True, stat_only=True)
    cmd = driver.commands[0]
    assert "git diff --stat" in cmd
    assert "--cached" in cmd
    assert "-- 'a.py'" in cmd


@pytest.mark.asyncio
async def test_run_tests_parses_pass_and_fail() -> None:
    driver = FakeDriver(
        [
            ExecResult(0, "__ROCKET_FRAMEWORK__:pytest\n3 passed in 0.42s\n", "", 1),
            ExecResult(1, "__ROCKET_FRAMEWORK__:pytest\n1 failed, 2 passed\n", "", 1),
        ]
    )
    passed = await tier2.run_tests(driver, "s1")
    assert "Tests PASSED" in passed
    assert "pytest" in passed
    assert "3 passed" in passed

    failed = await tier2.run_tests(driver, "s1")
    assert "Tests FAILED (exit 1)" in failed
    assert "1 failed" in failed


@pytest.mark.asyncio
async def test_run_tests_with_explicit_command_skips_detection() -> None:
    driver = FakeDriver([ExecResult(0, "ok", "", 1)])
    out = await tier2.run_tests(driver, "s1", command="make test")
    assert driver.commands[0] == "make test"
    assert "[custom]" in out


@pytest.mark.asyncio
async def test_registry_registers_and_dispatches_repo_tools() -> None:
    """The new tools are exposed to the model and dispatchable through FastMCP."""
    from agent_core.tools.registry import ToolRegistry

    driver = FakeDriver([ExecResult(0, "src/a.py\n", "", 1)])
    registry = ToolRegistry(driver=driver, session_id="s1")

    names = {definition.name for definition in registry.get_tool_definitions()}
    assert {
        "grep_search",
        "glob_files",
        "list_dir",
        "git_status",
        "git_diff",
        "run_tests",
    } <= names

    output = await registry.call_tool("glob_files", {"pattern": "src/**/*.py"})
    assert "src/a.py" in output


# --------------------------------------------------------------------------- #
# Per-file unified-diff application
# --------------------------------------------------------------------------- #


MULTI_FILE_DIFF = """diff --git a/a.py b/a.py
index 1111111..2222222 100644
--- a/a.py
+++ b/a.py
@@ -1,2 +1,2 @@
-old a
+new a
diff --git a/b.py b/b.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/b.py
@@ -0,0 +1,2 @@
+line1
+line2
"""


class PatchDriver:
    """Records patch application and replays read_file results for apply_diff_file."""

    def __init__(self, read_results: list[str] | None = None, apply_ok: bool = True) -> None:
        self.reads: list[str] = []
        self.writes: list[tuple[str, str, bool]] = []
        self.patches: list[tuple[str, str]] = []
        self._read_results = list(read_results or [])
        self._apply_ok = apply_ok

    async def read_file(
        self,
        session_id: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        self.reads.append(path)
        if self._read_results:
            return self._read_results.pop(0)
        return ""

    async def write_file(
        self, session_id: str, path: str, content: str, overwrite: bool = True
    ) -> None:
        self.writes.append((path, content, overwrite))

    async def apply_patch(self, session_id: str, path: str, patch_content: str) -> bool:
        self.patches.append((path, patch_content))
        return self._apply_ok


def test_split_file_patches_handles_git_and_new_files() -> None:
    patches = tier2.split_file_patches(MULTI_FILE_DIFF)
    assert [patch.path for patch in patches] == ["a.py", "b.py"]
    assert patches[0].is_new_file is False
    assert patches[1].is_new_file is True


def test_split_file_patches_supports_plain_marker_diffs() -> None:
    plain = (
        "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
        "--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-x\n+y\n"
    )
    assert [patch.path for patch in tier2.split_file_patches(plain)] == ["a.py", "b.py"]


def test_extract_file_patch_selects_a_single_file() -> None:
    patch = tier2.extract_file_patch(MULTI_FILE_DIFF, "a.py")
    assert patch is not None
    assert "+new a" in patch.content
    assert "b.py" not in patch.content


def test_extract_file_patch_reconstructs_new_file_content() -> None:
    patch = tier2.extract_file_patch(MULTI_FILE_DIFF, "b.py")
    assert patch is not None
    assert patch.is_new_file
    assert tier2._added_lines(patch.content) == "line1\nline2\n"


def test_extract_file_patch_missing_file_returns_none() -> None:
    assert tier2.extract_file_patch(MULTI_FILE_DIFF, "missing.py") is None


@pytest.mark.asyncio
async def test_apply_diff_file_only_touches_requested_file() -> None:
    driver = PatchDriver(read_results=["old a\n", "new a\n"])
    message, diff = await tier2.apply_diff_file(driver, "s1", MULTI_FILE_DIFF, "a.py")
    assert "Applied patch to 'a.py'" in message
    assert driver.patches[0][0] == "a.py"
    assert "b.py" not in driver.patches[0][1]
    assert diff is not None
    assert diff.path == "a.py"
    assert diff.additions == 1


@pytest.mark.asyncio
async def test_apply_diff_file_creates_new_file() -> None:
    driver = PatchDriver()
    message, diff = await tier2.apply_diff_file(driver, "s1", MULTI_FILE_DIFF, "b.py")
    assert "Created 'b.py'" in message
    assert driver.writes == [("b.py", "line1\nline2\n", False)]
    assert driver.patches == []
    assert diff is not None
    assert diff.is_new_file


@pytest.mark.asyncio
async def test_apply_diff_file_reports_missing_file() -> None:
    driver = PatchDriver()
    message, diff = await tier2.apply_diff_file(driver, "s1", MULTI_FILE_DIFF, "missing.py")
    assert "no diff section found" in message
    assert diff is None


@pytest.mark.asyncio
async def test_apply_diff_file_reports_failed_hunk_match() -> None:
    driver = PatchDriver(read_results=["old a\n"], apply_ok=False)
    message, diff = await tier2.apply_diff_file(driver, "s1", MULTI_FILE_DIFF, "a.py")
    assert "hunks did not match" in message
    assert diff is None
