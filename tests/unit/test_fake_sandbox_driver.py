"""Unit tests for the in-memory ``FakeSandboxDriver`` used by tests and CI."""

from __future__ import annotations

import pytest
from sandbox_driver.exceptions import SandboxFilesystemError
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import (
    ExecResult,
    SandboxDriverProtocol,
    SandboxStatus,
    WorkspaceSpec,
)

SESSION = "session_fake_1"


def _spec(session_id: str = SESSION) -> WorkspaceSpec:
    return WorkspaceSpec(
        session_id=session_id,
        tenant_org_id="org-1",
        tenant_user_id="user-1",
        container_image="fake:latest",
    )


def test_fake_driver_satisfies_the_sandbox_protocol() -> None:
    """The fake must be a drop-in for ``SandboxDriverProtocol``."""
    assert isinstance(FakeSandboxDriver(), SandboxDriverProtocol)


@pytest.mark.asyncio
async def test_lifecycle_preserves_files_until_destroyed() -> None:
    """Hibernation keeps the workspace; destruction wipes it."""
    driver = FakeSandboxDriver()
    assert await driver.get_status(SESSION) is SandboxStatus.NON_EXISTENT

    await driver.ensure_workspace(_spec())
    assert await driver.get_status(SESSION) is SandboxStatus.HIBERNATED

    await driver.start_sandbox(SESSION)
    assert await driver.get_status(SESSION) is SandboxStatus.RUNNING
    await driver.write_file(SESSION, "notes.md", "kept across hibernation\n")

    await driver.hibernate_sandbox(SESSION)
    assert await driver.get_status(SESSION) is SandboxStatus.HIBERNATED
    assert "kept across hibernation\n" in await driver.read_file(SESSION, "notes.md")

    await driver.destroy_workspace(SESSION)
    assert await driver.get_status(SESSION) is SandboxStatus.DESTROYED
    assert driver.file_snapshot(SESSION) == {}


@pytest.mark.asyncio
async def test_ensure_workspace_is_idempotent_and_seeds_files() -> None:
    """Calling ensure twice must not reset user edits."""
    driver = FakeSandboxDriver()
    await driver.ensure_workspace(_spec())
    await driver.write_file(SESSION, "README.md", "edited\n")
    await driver.ensure_workspace(_spec())
    await driver.ensure_workspace(_spec())

    assert await driver.read_file(SESSION, "README.md") == "edited\n"


@pytest.mark.asyncio
async def test_read_file_supports_line_ranges_and_normalised_paths() -> None:
    """Paths may be absolute or relative, and slices are 1-based inclusive."""
    driver = FakeSandboxDriver()
    await driver.write_file(SESSION, "src/app.py", "line1\nline2\nline3\nline4\n")

    assert (
        await driver.read_file(SESSION, "/workspace/src/app.py") == "line1\nline2\nline3\nline4\n"
    )
    assert await driver.read_file(SESSION, "./src/app.py", 2, 3) == "line2\nline3\n"


@pytest.mark.asyncio
async def test_missing_file_and_no_overwrite_raise() -> None:
    """Filesystem failures surface as ``SandboxFilesystemError``."""
    driver = FakeSandboxDriver(seed_files={})
    with pytest.raises(SandboxFilesystemError):
        await driver.read_file(SESSION, "absent.py")

    await driver.write_file(SESSION, "present.py", "x\n")
    with pytest.raises(SandboxFilesystemError):
        await driver.write_file(SESSION, "present.py", "y\n", overwrite=False)


@pytest.mark.asyncio
async def test_list_files_is_sorted_and_depth_limited() -> None:
    """Listings are deterministic and respect depth."""
    driver = FakeSandboxDriver(seed_files={"a/b/c/d.py": "x\n", "top.py": "y\n"})
    await driver.ensure_workspace(_spec())

    shallow = await driver.list_files(SESSION, max_depth=1)
    assert [(stat.path, stat.is_dir) for stat in shallow] == [
        ("/workspace/a", True),
        ("/workspace/top.py", False),
    ]

    deep = await driver.list_files(SESSION, max_depth=4)
    assert [stat.path for stat in deep] == [
        "/workspace/a",
        "/workspace/a/b",
        "/workspace/a/b/c",
        "/workspace/a/b/c/d.py",
        "/workspace/top.py",
    ]
    assert deep[3].size_bytes == len("x\n")

    inside = await driver.list_files(SESSION, "/workspace/a", max_depth=1)
    assert [stat.path for stat in inside] == ["/workspace/a/b"]

    single = await driver.list_files(SESSION, "top.py", max_depth=1)
    assert [(stat.path, stat.is_dir) for stat in single] == [("/workspace/top.py", False)]


@pytest.mark.asyncio
async def test_canned_command_output_is_deterministic() -> None:
    """Verification commands return stable, parseable output."""
    driver = FakeSandboxDriver()

    pytest_result = await driver.exec_command(SESSION, "uv run pytest -q")
    ruff_result = await driver.exec_command(SESSION, "uv run ruff check .")
    git_result = await driver.exec_command(SESSION, "git status")

    assert pytest_result.exit_code == 0
    assert "3 passed" in pytest_result.stdout
    assert "All checks passed" in ruff_result.stdout
    assert "nothing to commit" in git_result.stdout


@pytest.mark.asyncio
async def test_shell_helpers_cover_the_harness_command_shapes() -> None:
    """echo/cat/ls/find are modelled so repo-intelligence tools work."""
    driver = FakeSandboxDriver(seed_files={"src/app.py": "print(1)\n"})

    assert (await driver.exec_command(SESSION, "echo hello")).stdout == "hello\n"
    await driver.exec_command(SESSION, "echo written > notes.txt")
    assert await driver.read_file(SESSION, "notes.txt") == "written\n"
    assert (await driver.exec_command(SESSION, "cat src/app.py")).stdout == "print(1)\n"
    assert "src" in (await driver.exec_command(SESSION, "ls")).stdout
    found = await driver.exec_command(SESSION, "find /workspace -type f -name '*.py' -print")
    assert found.stdout.splitlines() == ["/workspace/src/app.py"]


@pytest.mark.asyncio
async def test_unknown_command_fails_loudly() -> None:
    """Unmodelled commands must not silently succeed."""
    result = await FakeSandboxDriver().exec_command(SESSION, "terraform apply -auto-approve")

    assert result.exit_code == 127
    assert "fake driver: unhandled command" in result.stderr


@pytest.mark.asyncio
async def test_scripted_results_take_precedence() -> None:
    """A scripted result overrides the canned handler, for failure-path tests."""
    driver = FakeSandboxDriver()
    driver.script_command("pytest", ExecResult(1, "", "1 failed\n", 7))

    result = await driver.exec_command(SESSION, "uv run pytest")

    assert result.exit_code == 1
    assert result.stderr == "1 failed\n"


@pytest.mark.asyncio
async def test_stdout_chunks_stream_in_order() -> None:
    """Chunk callbacks mirror the real driver's live log streaming."""
    driver = FakeSandboxDriver()
    chunks: list[str] = []
    expected = (await driver.exec_command(SESSION, "uv run pytest")).stdout

    await driver.exec_command(SESSION, "uv run pytest", on_stdout_chunk=chunks.append)

    assert "".join(chunks) == expected


@pytest.mark.asyncio
async def test_apply_patch_uses_the_shared_patch_engine() -> None:
    """The fake reuses the driver-neutral unified-diff engine."""
    driver = FakeSandboxDriver(seed_files={"app.py": "one\ntwo\nthree\n"})
    patch = "@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n"

    assert await driver.apply_patch(SESSION, "app.py", patch) is True
    assert await driver.read_file(SESSION, "app.py") == "one\nTWO\nthree\n"

    assert await driver.apply_patch(SESSION, "app.py", "not a patch") is False
    assert await driver.apply_patch(SESSION, "missing.py", patch) is False


@pytest.mark.asyncio
async def test_exec_calls_form_an_audit_trail() -> None:
    """Every executed command is recorded, so tests can assert what ran."""
    driver = FakeSandboxDriver()
    await driver.exec_command(SESSION, "ls")
    await driver.exec_command(SESSION, "git status")

    assert driver.exec_calls == [(SESSION, "ls"), (SESSION, "git status")]


@pytest.mark.asyncio
async def test_reconcile_and_cleanup_are_noops() -> None:
    """Lifecycle parity methods are safe to await from the reaper loop."""
    driver = FakeSandboxDriver()

    assert await driver.reconcile_inactivity() == ([], [])
    assert await driver.cleanup_warm_pool() is None
