"""The agent clones repositories itself: the sandbox starts blank."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.permissions import approval_needed
from agent_core.tools.registry import ToolRegistry
from agent_core.tools.repository import clone_repository
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
    assert "git clone --depth 50 https://github.com/acme/api.git /workspace/api" in command
    assert "ALREADY_CLONED" in command, "an existing clone is fetched, not an error"
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
