"""AGENTS.md / CLAUDE.md in the sandbox reach the agent's prompt."""

from __future__ import annotations

import pytest
from api.services.project_instructions import candidate_paths, load_project_instructions
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import ResourceLimits, WorkspaceSpec


async def _driver(files: dict[str, str]) -> FakeSandboxDriver:
    driver = FakeSandboxDriver()
    await driver.ensure_workspace(
        WorkspaceSpec("s1", "org", "user", "img", resources=ResourceLimits())
    )
    await driver.start_sandbox("s1")
    for path, content in files.items():
        await driver.write_file("s1", path, content)
    return driver


def test_repository_checkout_is_searched_before_the_workspace_root() -> None:
    assert candidate_paths("https://github.com/acme/api.git") == [
        "/workspace/api/AGENTS.md",
        "/workspace/api/CLAUDE.md",
        "/workspace/AGENTS.md",
        "/workspace/CLAUDE.md",
    ]
    assert candidate_paths(None) == ["/workspace/AGENTS.md", "/workspace/CLAUDE.md"]


@pytest.mark.asyncio
async def test_agents_md_wins_over_claude_md_and_is_labelled_with_its_path() -> None:
    driver = await _driver({"/workspace/AGENTS.md": "Use uv.", "/workspace/CLAUDE.md": "other"})
    section = await load_project_instructions(driver, "s1", None)
    assert section is not None
    assert "`/workspace/AGENTS.md`" in section and "Use uv." in section
    assert "other" not in section


@pytest.mark.asyncio
async def test_missing_or_blank_instructions_yield_nothing() -> None:
    assert await load_project_instructions(await _driver({}), "s1", None) is None
    blank = await _driver({"/workspace/AGENTS.md": "  \n"})
    assert await load_project_instructions(blank, "s1", None) is None


@pytest.mark.asyncio
async def test_oversized_instructions_are_clamped_keeping_both_ends() -> None:
    body = "FIRST\n" + "filler line\n" * 2000 + "LAST\n"
    section = await load_project_instructions(
        await _driver({"/workspace/AGENTS.md": body}), "s1", None
    )
    assert section is not None
    assert "FIRST" in section and "LAST" in section and len(section) < 20_000
