"""An agent with ``sandbox_enabled`` off: no sandbox tools, no sandbox, files kept with the session."""

from __future__ import annotations

from typing import Any

from api.models import AgentPersonaModel
from api.services.personas import save_persona
from api.services.session_files import get_session_file_store
from turn_harness import TurnOutcome, run_turn

from specifications.interfaces.sandbox import SandboxStatus


async def _run(org: str, *, sandbox_enabled: bool, script: list[Any]) -> TurnOutcome:
    persona = AgentPersonaModel(
        id=f"persona-{org}",
        name="Analyst",
        role_title="Data analyst",
        system_prompt="You answer questions.",
        scope="org",
        sandbox_enabled=sandbox_enabled,
    )
    saved = await save_persona(None, org, "admin", True, persona)
    return await run_turn(org, metadata={"agent_id": saved.id}, script=script)


def _offered(outcome: TurnOutcome) -> set[str]:
    request = next(r for r in outcome.model.requests if r.tools)
    return {tool.name for tool in request.tools}


def _system(outcome: TurnOutcome) -> str:
    request = next(r for r in outcome.model.requests if r.tools)
    return "\n".join(m.content or "" for m in request.messages if m.role == "system")


async def test_an_agent_without_a_sandbox_keeps_its_notes_and_never_starts_one() -> None:
    org = "no_sandbox"
    outcome = await _run(
        org,
        sandbox_enabled=False,
        script=[
            ("file_write", {"path": "notes/plan.md", "content": "# plan\n- step one\n"}),
            (
                "file_edit",
                {
                    "path": "notes/plan.md",
                    "target_block": "- step one",
                    "replacement_block": "- step two",
                },
            ),
            ("list_dir", {"path": "notes"}),
            ("bash_exec", {"command": "ls"}),
        ],
    )

    offered = _offered(outcome)
    assert {"file_read", "file_write", "file_edit"} <= offered
    assert not offered & {"bash_exec", "clone_repository", "git_status", "grep_search"}
    assert "WITHOUT a sandbox" in _system(outcome)
    assert "[Workspace]" not in _system(outcome)
    kept = await get_session_file_store(None).get(f"session_{org}", org, "notes/plan.md")
    assert kept is not None and kept.content == "# plan\n- step two\n" and kept.kind == "scratch"
    last = [r for r in outcome.model.requests if r.tools][-1]
    results = [m.content or "" for m in last.messages if m.role == "tool"]
    assert any("notes/plan.md" in r and "Session files" in r for r in results)
    assert "runs without one" in results[-1]
    assert await outcome.driver.get_status(f"session_{org}") == SandboxStatus.NON_EXISTENT


async def test_an_agent_with_a_sandbox_keeps_its_sandbox_tools() -> None:
    outcome = await _run("with_sandbox", sandbox_enabled=True, script=[])

    assert "bash_exec" in _offered(outcome) or "bash_exec" in _system(outcome)
    assert "WITHOUT a sandbox" not in _system(outcome)


async def test_nothing_can_start_a_sandbox_for_an_agent_without_one() -> None:
    from agent_core.context import ExecutionContext, current_execution_ctx
    from agent_core.tools.registry import SandboxDisabledError, ToolRegistry
    from sandbox_driver.fake_driver import FakeSandboxDriver

    from specifications.interfaces.agent import TurnCallbacks, TurnPolicy

    started: list[str] = []

    async def ensure(session_id: str) -> None:
        started.append(session_id)

    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id="s")
    ctx = ExecutionContext(
        session_id="s",
        event_queue=None,
        parent_model="m",
        policy=TurnPolicy(sandbox_enabled=False),
        callbacks=TurnCallbacks(ensure_sandbox=ensure),
    )
    token = current_execution_ctx.set(ctx)
    try:
        try:
            await registry._ensure_sandbox("s")
        except SandboxDisabledError as err:
            assert "without a sandbox" in str(err)
        else:
            raise AssertionError("a sandbox-free turn started a sandbox")
    finally:
        current_execution_ctx.reset(token)
    assert started == []
