"""The session's active repository: recorded once, carried by every turn, used by the tools."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from api.routers.webhooks import _sync_repository
from api.services.session_support import (
    ACTIVE_REPOSITORY_KEY,
    active_repository_of,
    choose_active_repository,
    remember_active_repository,
)
from api.store import InMemorySessionStore
from api.turns.runner import TurnRunner
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import ExecResult
from specifications.interfaces.session import SessionRecord
from tests.turn_harness import run_turn


class _WorkdirDriver(FakeSandboxDriver):
    """Records the folder each command ran in."""

    def __init__(self) -> None:
        super().__init__()
        self.workdirs: list[tuple[str, str]] = []
        self.script_command("command -v git", ExecResult(0, "/usr/bin/git\n", "", 1))
        self.script_command("git clone", ExecResult(0, "main\nabc123 Initial (now)\n", "", 1))

    async def exec_command(
        self, session_id: str, command: str, workdir: str = "/workspace", *a: Any, **k: Any
    ) -> ExecResult:
        self.workdirs.append((command, workdir))
        return await super().exec_command(session_id, command, workdir, *a, **k)


def _session(metadata: dict[str, Any] | None = None, git_repo: str | None = None) -> SessionRecord:
    return SessionRecord(
        session_id="s1",
        tenant_org_id="org",
        tenant_user_id="u1",
        title="t",
        metadata=metadata or {},
        git_repo=git_repo,
    )


def test_the_recorded_folder_wins_over_the_named_repository() -> None:
    assert active_repository_of(_session()) == ""
    assert (
        active_repository_of(_session(git_repo="https://github.com/acme/api")) == "/workspace/api"
    )
    recorded = _session({ACTIVE_REPOSITORY_KEY: "/workspace/web"}, "https://github.com/acme/api")
    assert active_repository_of(recorded) == "/workspace/web"


async def test_the_first_folder_is_kept() -> None:
    store = InMemorySessionStore()
    await store.create_session(_session())

    await remember_active_repository(store, "s1", "org", "/workspace/api")
    await remember_active_repository(store, "s1", "org", "/workspace/web")

    record = await store.get_session("s1")
    assert record is not None and record.metadata[ACTIVE_REPOSITORY_KEY] == "/workspace/api"


async def test_a_turn_runs_git_tools_in_the_recorded_repository() -> None:
    driver = _WorkdirDriver()

    await run_turn(
        "org_active",
        script=[("git_status", {})],
        metadata={ACTIVE_REPOSITORY_KEY: "/workspace/api"},
        driver=driver,
    )

    status_runs = [
        workdir for command, workdir in driver.workdirs if "status --porcelain" in command
    ]
    assert status_runs == ["/workspace/api"]


async def test_a_clone_during_a_turn_is_remembered_for_the_next_turns() -> None:
    outcome = await run_turn(
        "org_clone",
        script=[("clone_repository", {"repository": "acme/api"})],
        driver=_WorkdirDriver(),
    )

    assert outcome.session.metadata[ACTIVE_REPOSITORY_KEY] == "/workspace/api"


def _repositories_driver(*folders: str) -> _WorkdirDriver:
    driver = _WorkdirDriver()
    # The rule scripted last is matched first, so the general refusal goes in before the folders.
    driver.script_command("rev-parse --git-dir", ExecResult(128, "", "not a git repository", 1))
    for folder in folders:
        driver.script_command(
            f"git -C {folder} rev-parse --git-dir", ExecResult(0, ".git\n", "", 1)
        )
    return driver


async def test_relative_file_paths_are_inside_the_active_repository() -> None:
    driver = _WorkdirDriver()

    outcome = await run_turn(
        "org_relative",
        script=[
            ("file_write", {"path": "src/app.py", "content": "x = 1\n"}),
            ("file_write", {"path": "/workspace/notes.md", "content": "n\n"}),
            ("file_read", {"path": "README.md"}),
        ],
        metadata={ACTIVE_REPOSITORY_KEY: "/workspace/api"},
        driver=driver,
    )

    files = driver.file_snapshot("session_org_relative")
    assert files["api/src/app.py"] == "x = 1\n" and files["notes.md"] == "n\n"
    results = outcome.text_of_events()
    assert "'/workspace/api/src/app.py'" in results, "results show the absolute path used"
    assert "Relative paths are inside the active repository /workspace/api" in results
    diffs = [e.payload["path"] for e in outcome.events if e.event_type == "file_diff"]
    assert diffs == ["api/src/app.py", "notes.md"], "diff events stay relative to /workspace"


async def test_the_agent_can_switch_the_active_repository_for_later_turns() -> None:
    driver = _repositories_driver("/workspace/web")

    outcome = await run_turn(
        "org_switch",
        script=[
            ("set_active_repository", {"path": "/workspace/missing"}),
            ("set_active_repository", {"path": "web"}),
            ("git_status", {}),
        ],
        metadata={ACTIVE_REPOSITORY_KEY: "/workspace/api"},
        driver=driver,
    )

    assert "/workspace/missing is not a git repository" in outcome.text_of_events()
    assert outcome.session.metadata[ACTIVE_REPOSITORY_KEY] == "/workspace/web"
    status_runs = [w for c, w in driver.workdirs if "status --porcelain" in c]
    assert status_runs == ["/workspace/web"]


async def test_choosing_replaces_the_first_folder() -> None:
    store = InMemorySessionStore()
    await store.create_session(_session())

    await remember_active_repository(store, "s1", "org", "/workspace/api")
    await choose_active_repository(store, "s1", "org", "/workspace/web")

    record = await store.get_session("s1")
    assert record is not None and record.metadata[ACTIVE_REPOSITORY_KEY] == "/workspace/web"


async def test_a_webhook_follow_up_pulls_in_the_active_repository() -> None:
    driver = _repositories_driver("/workspace/api")

    await _sync_repository(driver, "s1", _session({ACTIVE_REPOSITORY_KEY: "/workspace/api"}))
    await _sync_repository(_repositories_driver(), "s1", _session())

    pulls = [w for c, w in driver.workdirs if "git pull" in c]
    assert pulls == ["/workspace/api"]


async def test_production_never_invents_a_session_for_a_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A turn whose session is missing used to create one owned by ``dev_user`` in
    ``default_org``; outside development it is an error instead."""
    monkeypatch.setenv("ROCKET_AUTO_PROVISION_TENANTS", "false")
    runner = TurnRunner(
        SimpleNamespace(store=InMemorySessionStore(), session_id="ghost", prompt="hi")
    )  # type: ignore[arg-type]

    with pytest.raises(LookupError):
        await runner._load_session()
