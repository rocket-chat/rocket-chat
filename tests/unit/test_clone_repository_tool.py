"""The agent clones repositories itself: the sandbox starts blank."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.permissions import approval_needed
from agent_core.tools.registry import ToolRegistry
from agent_core.tools.repository import clone_into_workspace, clone_repository
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.agent import TurnCallbacks
from specifications.interfaces.sandbox import ExecResult


class _Driver(FakeSandboxDriver):
    def __init__(self, *, git_installed: bool = True, clone: ExecResult | None = None) -> None:
        super().__init__()
        self.commands: list[str] = []
        self.git_installed = git_installed
        self.clone = clone or ExecResult(
            0,
            "Cloning into '/workspace/api'...\nmain\nabc1234 Initial (2 days ago)\n--- top level\nREADME.md\nsrc\n",
            "",
            1,
        )

    async def exec_command(self, session_id: str, command: str, *a: Any, **k: Any) -> ExecResult:
        self.commands.append(command)
        if command.startswith("command -v git"):
            return ExecResult(0 if self.git_installed else 1, "", "", 1)
        return self.clone


async def test_a_repository_is_cloned_into_its_own_folder_and_described() -> None:
    driver = _Driver()

    result = await clone_repository(driver, "s", "acme/api")

    assert "Cloned acme/api into /workspace/api" in result
    assert "abc1234 Initial" in result and "README.md" in result
    command = driver.commands[-1]
    assert (
        "git clone --filter=blob:none --no-tags https://github.com/acme/api.git /workspace/api"
        in command
    )
    assert "ALREADY_CLONED" in command, "an existing clone is fetched, not an error"
    assert "config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'" in command, (
        "heals single-branch clones on refresh"
    )
    assert "&& ( git rev-parse" in command, "the summary must be gated by the clone's success"


async def test_branch_and_folder_are_honoured_and_quoted() -> None:
    driver = _Driver()

    await clone_repository(
        driver, "s", "https://github.com/acme/api.git", branch="release/1.2", directory="api-v1"
    )

    assert "--branch release/1.2 " in driver.commands[-1]
    assert "/workspace/api-v1" in driver.commands[-1]


async def test_an_existing_clone_is_reported_as_refreshed() -> None:
    driver = _Driver(
        clone=ExecResult(0, "ALREADY_CLONED\nmain\nabc1234 Initial\n--- top level\nsrc\n", "", 1)
    )

    assert (await clone_repository(driver, "s", "acme/api")).startswith(
        "Already cloned (fetched the latest) acme/api"
    )


@pytest.mark.parametrize(
    "repository, branch, directory",
    [
        ("file:///etc", None, None),
        ("https://user:pw@github.com/acme/api", None, None),
        ("acme/api", "--upload-pack=x", None),
        ("acme/api", None, "../escape"),
        ("acme/api", None, "a/b"),
    ],
)
async def test_unsafe_input_never_reaches_the_shell(
    repository: str, branch: str | None, directory: str | None
) -> None:
    driver = _Driver()

    result = await clone_repository(driver, "s", repository, branch, directory)

    assert result.startswith("Error:")
    assert driver.commands == []


async def test_a_missing_or_private_repository_gets_guidance_not_a_token_hunt() -> None:
    driver = _Driver(
        clone=ExecResult(
            128,
            "fatal: could not read Username for 'https://github.com': terminal prompts disabled",
            "",
            1,
        )
    )

    result = await clone_repository(driver, "s", "acme/secret")

    assert "GitHub refused access to acme/secret" in result
    assert "Do not retry or hunt for tokens" in result
    assert "terminal prompts" not in result


async def test_other_failures_are_returned_with_their_output() -> None:
    driver = _Driver(
        clone=ExecResult(128, "fatal: unable to access: Could not resolve host", "", 1)
    )

    result = await clone_repository(driver, "s", "acme/api")

    assert result.startswith("Error: could not clone acme/api") and "resolve host" in result


async def test_a_sandbox_without_git_that_cannot_install_it_says_so() -> None:
    driver = _Driver(git_installed=False)

    result = await clone_repository(driver, "s", "acme/api")

    assert "git is not installed" in result
    assert len(driver.commands) == 1, "no clone is attempted without git"


def test_cloning_needs_no_approval_in_any_mode() -> None:
    for mode in ("plan", "ask", "auto_edit", "auto"):
        assert approval_needed(mode, "clone_repository", {"repository": "acme/api"}) is None


def _context(**overrides: Any) -> ExecutionContext:
    return ExecutionContext(
        session_id="s1",
        event_queue=asyncio.Queue(),
        parent_model="m",
        callbacks=TurnCallbacks(),
        **overrides,
    )


async def test_the_agent_sees_the_tool_and_may_use_it_even_in_plan_mode() -> None:
    driver = _Driver()
    registry = ToolRegistry(driver=driver, session_id="s1")

    token = current_execution_ctx.set(_context(plan_mode=True))
    try:
        assert "clone_repository" in {d.name for d in registry.get_tool_definitions()}
        result = await registry.call_tool("clone_repository", {"repository": "acme/api"})
    finally:
        current_execution_ctx.reset(token)

    assert "Cloned acme/api" in result, "plan mode must still be able to read the code"


async def test_a_fresh_clone_brings_the_repositorys_own_instructions_once() -> None:
    driver = _Driver()
    await driver.ensure_workspace(
        __import__("specifications.interfaces.sandbox", fromlist=["WorkspaceSpec"]).WorkspaceSpec(
            "s", "org", "user", "img"
        )
    )
    await driver.write_file("s", "/workspace/api/AGENTS.md", "Run `make test` before committing.")

    fresh = await clone_repository(driver, "s", "acme/api")
    assert "[Project Instructions - from `/workspace/api/AGENTS.md`]" in fresh
    assert "make test" in fresh

    driver.clone = ExecResult(
        0, "ALREADY_CLONED\nmain\nabc1234 Initial\n--- top level\nsrc\n", "", 1
    )
    repeat = await clone_repository(driver, "s", "acme/api")
    assert "Already cloned" in repeat and "Project Instructions" not in repeat


async def test_a_clone_without_instructions_adds_nothing() -> None:
    result = await clone_repository(_Driver(), "s", "acme/api")

    assert "Project Instructions" not in result


async def _run_cmd(*cmd: str, cwd: Path | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode(), stderr.decode()


async def test_clone_fetches_all_branches_and_heals_old_clones(tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    init_repo = tmp_path / "init_repo"
    init_repo.mkdir()
    await _run_cmd("git", "init", "-b", "main", str(init_repo))
    await _run_cmd("git", "config", "user.email", "test@example.com", cwd=init_repo)
    await _run_cmd("git", "config", "user.name", "Test", cwd=init_repo)
    (init_repo / "README.md").write_text("# Test")
    await _run_cmd("git", "add", "README.md", cwd=init_repo)
    await _run_cmd("git", "commit", "-m", "Initial commit", cwd=init_repo)

    await _run_cmd("git", "checkout", "-b", "beta", cwd=init_repo)
    (init_repo / "beta.txt").write_text("beta")
    await _run_cmd("git", "add", "beta.txt", cwd=init_repo)
    await _run_cmd("git", "commit", "-m", "Beta commit", cwd=init_repo)

    await _run_cmd("git", "checkout", "-b", "feature/x", cwd=init_repo)
    (init_repo / "feature.txt").write_text("feature")
    await _run_cmd("git", "add", "feature.txt", cwd=init_repo)
    await _run_cmd("git", "commit", "-m", "Feature commit", cwd=init_repo)

    await _run_cmd("git", "clone", "--bare", str(init_repo), str(origin))

    (tmp_path / "workspace").mkdir(exist_ok=True)

    class RealExecDriver(FakeSandboxDriver):
        async def exec_command(
            self, session_id: str, command: str, *a: Any, **k: Any
        ) -> ExecResult:
            if command.startswith("command -v git"):
                return ExecResult(0, "/usr/bin/git", "", 1)
            mapped = command.replace("/workspace", str(tmp_path / "workspace"))
            mapped = mapped.replace("https://github.com/acme/repo.git", str(origin))
            rc, out, err = await _run_cmd("bash", "-c", mapped)
            return ExecResult(rc, out, err, 1)

    driver = RealExecDriver()

    # 1. Fresh clone: origin/beta and origin/feature/x must exist
    res = await clone_into_workspace(driver, "s", "acme/repo", directory="fresh")
    assert "Cloned acme/repo" in res.message
    fresh_target = tmp_path / "workspace" / "fresh"
    assert fresh_target.exists()
    code_beta, _, _ = await _run_cmd("git", "-C", str(fresh_target), "rev-parse", "origin/beta")
    assert code_beta == 0
    code_feat, _, _ = await _run_cmd(
        "git", "-C", str(fresh_target), "rev-parse", "origin/feature/x"
    )
    assert code_feat == 0

    # 2. Existing single-branch clone: heal on refresh
    single_target = tmp_path / "workspace" / "single"
    await _run_cmd(
        "git",
        "clone",
        "--single-branch",
        "--branch",
        "main",
        str(origin),
        str(single_target),
    )
    # Before healing, origin/beta fails
    code_before, _, _ = await _run_cmd("git", "-C", str(single_target), "rev-parse", "origin/beta")
    assert code_before != 0

    # Refresh via clone_into_workspace (hits ALREADY_CLONED branch, heals config and fetches)
    res_healed = await clone_into_workspace(driver, "s", "acme/repo", directory="single")
    assert "Already cloned" in res_healed.message
    code_after, _, _ = await _run_cmd("git", "-C", str(single_target), "rev-parse", "origin/beta")
    assert code_after == 0
