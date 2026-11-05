"""The folder a command ran in reaches the cockpit, even when the call named none."""

from __future__ import annotations

from api.services.session_support import ACTIVE_REPOSITORY_KEY
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import ExecResult
from tests.turn_harness import run_turn


async def test_a_command_without_a_folder_reports_the_one_it_ran_in() -> None:
    driver = FakeSandboxDriver()
    driver.script_command("test -d", ExecResult(0, "", "", 1))
    outcome = await run_turn(
        "org_folder",
        script=[("bash_exec", {"command": "make test", "cwd": ""})],
        metadata={ACTIVE_REPOSITORY_KEY: "/workspace/api"},
        driver=driver,
    )

    completed = [e for e in outcome.events_of("tool_completed") if e.payload["name"] == "bash_exec"]
    assert completed and completed[0].payload["cwd"] == "/workspace/api"
    assert completed[0].payload["arguments"]["cwd"] == "", "the model's own arguments are kept"
    stored = [
        call
        for message in outcome.session.conversation_history
        for call in (message.tool_calls or [])
        if call["name"] == "bash_exec"
    ]
    assert stored and stored[0]["cwd"] == "/workspace/api", "a reloaded conversation shows it"


async def test_a_leading_cd_is_reported_as_the_folder() -> None:
    outcome = await run_turn(
        "org_folder_cd",
        script=[("bash_exec", {"command": "cd web && pnpm build", "cwd": "/workspace"})],
    )

    completed = [e for e in outcome.events_of("tool_completed") if e.payload["name"] == "bash_exec"]
    assert completed and completed[0].payload["cwd"] == "/workspace/web"


async def test_a_tool_that_runs_no_command_reports_no_folder() -> None:
    outcome = await run_turn("org_folder_none", script=[("list_dir", {"path": "/workspace"})])

    completed = outcome.events_of("tool_completed")
    assert completed and all("cwd" not in e.payload for e in completed)


async def test_a_delegates_command_is_its_own_not_the_delegation_call() -> None:
    driver = FakeSandboxDriver()
    driver.script_command("stash create", ExecResult(0, "stash_abc123\n", "", 1))

    outcome = await run_turn(
        "org_folder_child",
        script=[
            ("delegate_subagent", {"role": "qa_verifier", "task": "Run the tests"}),
            ("bash_exec", {"command": "pytest -q", "cwd": ""}),
        ],
        metadata={ACTIVE_REPOSITORY_KEY: "/workspace/repo"},
        driver=driver,
    )

    completed = outcome.events_of("tool_completed")
    child = [
        e for e in completed if e.payload.get("subagent_id") and e.payload["name"] == "bash_exec"
    ]
    delegation = [e for e in completed if e.payload["name"] == "delegate_subagent"]
    assert child and child[0].payload.get("cwd"), "the delegate's step says where it ran"
    assert delegation and "cwd" not in delegation[0].payload
