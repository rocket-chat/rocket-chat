"""Once a repository is in the sandbox the agent reads it there, not through the GitHub API."""

from __future__ import annotations

from agent_core.tools.registry import ToolRegistry
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import ExecResult, WorkspaceSpec

REPO = "acme/api"


async def _registry(driver: FakeSandboxDriver, started: bool) -> ToolRegistry:
    await driver.ensure_workspace(
        WorkspaceSpec(session_id="s1", tenant_org_id="o", tenant_user_id="u", container_image="img")
    )
    if started:
        await driver.start_sandbox("s1")
    return ToolRegistry(driver=driver, session_id="s1")


def _clone_listing(driver: FakeSandboxDriver, origin: str) -> None:
    driver.script_command("remote get-url", ExecResult(0, f"/workspace/api/ {origin}\n", "", 1))


async def _read(registry: ToolRegistry, **arguments: object) -> str:
    arguments.setdefault("repository", REPO)
    return await registry.call_tool("github_read_file", arguments)


async def test_a_cloned_repository_is_read_in_the_sandbox() -> None:
    driver = FakeSandboxDriver()
    _clone_listing(driver, "https://x-access-token:SECRET@github.com/acme/api.git")
    registry = await _registry(driver, started=True)

    message = await _read(registry, path="README.md")

    assert "already cloned at /workspace/api" in message and "file_read" in message
    assert "SECRET" not in message


async def test_another_ref_still_reads_from_github() -> None:
    from unittest.mock import AsyncMock, patch

    driver = FakeSandboxDriver()
    _clone_listing(driver, "https://github.com/acme/api.git")
    registry = await _registry(driver, started=True)

    with patch(
        "agent_core.tools.github_tools.read_file", AsyncMock(return_value="remote content")
    ) as remote:
        assert await _read(registry, path="README.md", ref="release") == "remote content"
    remote.assert_awaited_once()


async def test_asking_never_starts_a_sandbox_and_other_repositories_are_not_redirected() -> None:
    from unittest.mock import AsyncMock, patch

    stopped = FakeSandboxDriver()
    _clone_listing(stopped, "https://github.com/acme/api.git")
    registry = await _registry(stopped, started=False)
    running = FakeSandboxDriver()
    _clone_listing(running, "https://github.com/acme/other.git")
    other = await _registry(running, started=True)

    with patch("agent_core.tools.github_tools.read_file", AsyncMock(return_value="remote")):
        assert await _read(registry) == "remote", "a stopped sandbox is not consulted"
        assert await _read(other) == "remote", "a different repository is read from GitHub"
    assert stopped.exec_calls == []
