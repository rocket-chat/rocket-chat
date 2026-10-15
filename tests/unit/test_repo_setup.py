"""Per-repository setup after the clone."""

from __future__ import annotations

from typing import Any

import pytest
from api.services.repo_setup import (
    DEVCONTAINER,
    MARKER_DIR,
    SETUP_SCRIPT,
    SetupPlan,
    find_setup_plan,
    plan_from_devcontainer,
    run_setup,
)
from api.turns.provisioning import run_repository_setup
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import ExecResult

# --- reading the repository's intent ------------------------------------------------------


def test_devcontainer_hooks_are_read_in_every_form_including_comments() -> None:
    config = """
    {
      // dev container for the widgets repo
      "image": "ignored:latest",
      "onCreateCommand": "uv sync",
      "postCreateCommand": { "web": "pnpm install", "tools": ["pre-commit", "install"] },
      "features": {},
    }
    """
    plan = plan_from_devcontainer(config)
    assert plan is not None
    assert plan.command == "uv sync && pnpm install && pre-commit install"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[]",
        '{"image": "x"}',
        '{"postCreateCommand": 5}',
        '{"postCreateCommand": ""}',
    ],
)
def test_a_devcontainer_without_usable_hooks_means_no_setup(text: str) -> None:
    assert plan_from_devcontainer(text) is None


def test_an_oversized_command_is_ignored() -> None:
    assert plan_from_devcontainer('{"postCreateCommand": "%s"}' % ("x" * 9000)) is None


async def test_the_repository_script_beats_the_devcontainer() -> None:
    driver = FakeSandboxDriver(
        seed_files={
            SETUP_SCRIPT: "pip install -e .\n",
            DEVCONTAINER: '{"postCreateCommand": "make"}',
        }
    )
    plan = await find_setup_plan(driver, "s1")
    assert plan is not None and plan.source == SETUP_SCRIPT


async def test_no_files_means_no_setup() -> None:
    assert await find_setup_plan(FakeSandboxDriver(seed_files={"README.md": "x"}), "s1") is None


# --- running it ---------------------------------------------------------------------------


class _Driver(FakeSandboxDriver):
    def __init__(self, setup_exit: int = 0, **kw: Any) -> None:
        super().__init__(**kw)
        self.setup_exit = setup_exit
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.markers: dict[str, str] = {}

    async def exec_command(self, session_id: str, command: str, *a: Any, **k: Any) -> ExecResult:
        env = dict(k.get("env") or {})
        if command.startswith("cat ") and MARKER_DIR in command:
            marker = self.markers.get(command.split("cat ", 1)[1].split(" ", 1)[0])
            return ExecResult(0 if marker else 1, marker or "", "", 1)
        if "printf %s" in command and MARKER_DIR in command:
            fingerprint = command.split("printf %s ", 1)[1].split(" >")[0].strip("'")
            self.markers[command.split(" > ", 1)[1]] = fingerprint
            return ExecResult(0, "", "", 1)
        if command.startswith(("command -v git", "git rev-parse", "mkdir")):
            return ExecResult(0, "", "", 1)
        self.calls.append((command, env))
        return ExecResult(
            self.setup_exit, "installed 3 packages\n", "warn\n" if self.setup_exit else "", 1
        )


PLAN = SetupPlan(source=SETUP_SCRIPT, command="sh .rocket/setup.sh")


async def test_setup_runs_once_per_workspace_and_without_git_credentials() -> None:
    driver = _Driver()
    ok, output = await run_setup(driver, "s1", PLAN)
    assert ok and "installed 3 packages" in output
    env = driver.calls[0][1]
    assert env["GITHUB_TOKEN"] == env["GH_TOKEN"] == env["GIT_ASKPASS"] == ""
    # No gateway capability either: the repository's own script can only fetch public code.
    assert env["GIT_CONFIG_COUNT"] == "0"
    assert all(env[f"GIT_CONFIG_VALUE_{i}"] == "" for i in range(4))

    again, note = await run_setup(driver, "s1", PLAN)
    assert again and note == "already done" and len(driver.calls) == 1


async def test_a_changed_setup_runs_again() -> None:
    driver = _Driver()
    await run_setup(driver, "s1", PLAN)
    await run_setup(driver, "s1", SetupPlan(source=DEVCONTAINER, command="make deps"))
    assert [c for c, _ in driver.calls] == ["sh .rocket/setup.sh", "make deps"]


async def test_a_failed_setup_is_reported_and_retried_next_time() -> None:
    driver = _Driver(setup_exit=2)
    ok, output = await run_setup(driver, "s1", PLAN)
    assert not ok and "warn" in output and not driver.markers


# --- as part of the agent cloning a repository ---------------------------------------------

FOLDER = "/workspace/widgets"


def _setup_calls(driver: _Driver) -> list[str]:
    """The commands that reached the sandbox beyond the clone's own preparation."""
    return [c for c, _ in driver.calls]


async def _after_clone(driver: FakeSandboxDriver) -> tuple[str | None, list[str]]:
    from api.events import AsyncIOEventBus

    bus = AsyncIOEventBus()
    note = await run_repository_setup(driver, "session_abc123", FOLDER, bus)
    events, _ = bus.replay_since("session_abc123", 0)
    return note, [str(e.payload.get("message")) for e in events]


async def test_setup_runs_in_the_cloned_folder_and_the_agent_is_told() -> None:
    driver = _Driver(seed_files={f"widgets/{SETUP_SCRIPT}": "make deps\n"})
    note, messages = await _after_clone(driver)
    assert any("Running repository setup (.rocket/setup.sh)" in m for m in messages)
    assert _setup_calls(driver) == ["sh .rocket/setup.sh"]
    assert note is not None and "completed" in note


async def test_a_repository_without_setup_says_nothing() -> None:
    note, messages = await _after_clone(_Driver(seed_files={"widgets/README.md": "x"}))
    assert note is None and messages == []


async def test_a_failing_setup_is_reported_to_the_agent() -> None:
    driver = _Driver(setup_exit=1, seed_files={f"widgets/{SETUP_SCRIPT}": "false\n"})
    note, _ = await _after_clone(driver)
    assert note is not None and "failed" in note


async def test_two_repositories_keep_separate_setup_markers() -> None:
    driver = _Driver()
    await run_setup(driver, "s1", PLAN, "/workspace/a")
    await run_setup(driver, "s1", PLAN, "/workspace/b")
    assert len(driver.calls) == 2
