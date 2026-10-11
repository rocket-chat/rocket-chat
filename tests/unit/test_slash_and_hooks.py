"""Slash commands expand prompt skills; organization hooks gate and follow tool calls."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.services import skills_lookup
from api.services.slash_commands import expand_skill, parse_slash_command
from api.services.turn_service import parse_hooks
from api.turns import provisioning, runner
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.agent import HookSpec, TurnPolicy
from specifications.interfaces.llm import LLMGatewayProtocol, StreamChunk
from specifications.interfaces.sandbox import ExecResult, WorkspaceSpec

# --- slash commands -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt, expected",
    [
        ("/review", ("review", "")),
        ("/review src/app.py and tests", ("review", "src/app.py and tests")),
        ("  /fix-bug  login fails\nsecond line", ("fix-bug", "login fails\nsecond line")),
        ("/etc/hosts is odd", None),
        ("explain /review", None),
        ("/", None),
        ("//double", None),
    ],
)
def test_only_a_leading_command_word_is_parsed(
    prompt: str, expected: tuple[str, str] | None
) -> None:
    parsed = parse_slash_command(prompt)
    assert (None if parsed is None else (parsed.name, parsed.arguments)) == expected


def test_arguments_fill_the_placeholder_or_follow_the_body() -> None:
    assert expand_skill("Review $ARGUMENTS carefully", "src/") == "Review src/ carefully"
    assert expand_skill("Review the diff.", "src/") == "Review the diff.\n\nArguments: src/"
    assert expand_skill("Review the diff.", "") == "Review the diff."


@pytest.fixture
def skills(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        skills_lookup,
        "_memory_skills",
        {
            "org_a": {
                "sk1": {
                    "name": "review",
                    "scope": "org",
                    "runtime": "text",
                    "body": "Review $ARGUMENTS.",
                },
                "sk2": {"name": "deploy", "scope": "org", "runtime": "text", "body": "Ship it."},
                "sk3": {
                    "name": "pyskill",
                    "scope": "org",
                    "runtime": "python",
                    "code": "x",
                    "body": "",
                },
            }
        },
    )


async def test_a_known_text_skill_is_expanded_for_the_model(skills: None) -> None:
    out = await provisioning._expand_slash_command("/review src/", None, "org_a", "u", ())
    assert out == "Review src/."


@pytest.mark.parametrize("prompt", ["/unknown thing", "/pyskill", "/etc/hosts", "plain text"])
async def test_everything_else_reaches_the_model_untouched(skills: None, prompt: str) -> None:
    assert await provisioning._expand_slash_command(prompt, None, "org_a", "u", ()) == prompt


async def test_a_persona_restricted_to_other_skills_cannot_expand_this_one(skills: None) -> None:
    assert (
        await provisioning._expand_slash_command("/deploy", None, "org_a", "u", ("sk1",))
        == "/deploy"
    )
    assert (
        await provisioning._expand_slash_command("/review x", None, "org_a", "u", ("sk1",))
        == "Review x."
    )


async def test_the_conversation_keeps_what_the_person_typed(skills: None) -> None:
    from api.events import AsyncIOEventBus
    from api.services.settings import save_org_config
    from api.store import InMemorySessionStore

    from specifications.interfaces.session import SessionRecord

    seen: list[str] = []

    class Gateway(LLMGatewayProtocol):
        async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
            seen.append(str(request.messages[-1].content))
            yield StreamChunk(text_delta="ok")

        async def check_budget(self, *a: Any) -> bool:
            return True

        async def record_usage(self, *a: Any, **k: Any) -> None:
            return None

    await save_org_config(None, "org_a", "governance", {}, [])
    store = InMemorySessionStore()
    await store.create_session(
        SessionRecord(session_id="s_slash", tenant_org_id="org_a", tenant_user_id="u", title="t")
    )
    driver = FakeSandboxDriver()
    registry = ToolRegistry(driver=driver, session_id="s_slash")
    async with asyncio.timeout(20):
        await runner.execute_turn_background(
            session_id="s_slash",
            prompt="/review src/",
            orchestrator=AsyncReActOrchestrator(gateway=Gateway(), registry=registry),
            event_bus=AsyncIOEventBus(),
            driver=driver,
            registry=registry,
            store=store,
        )
    history = (await store.get_session("s_slash")).conversation_history  # type: ignore[union-attr]
    assert [m.content for m in history if m.role == "user"] == ["/review src/"]
    assert seen[0] == "Review src/."


# --- hooks --------------------------------------------------------------------------------


def test_malformed_hook_settings_are_dropped_not_fatal() -> None:
    hooks = parse_hooks(
        {
            "pre_tool": [
                {"match": "bash_exec", "command": "./gate.sh", "timeout_seconds": 9999},
                {"command": "  "},
                "garbage",
                {"match": "file_*", "command": "echo ok"},
            ],
            "post_tool": [{"command": "make fmt", "timeout_seconds": "soon"}],
            "bogus_event": [{"command": "x"}],
        }
    )
    assert [(h.event, h.match, h.command, h.timeout_seconds) for h in hooks] == [
        ("pre_tool", "bash_exec", "./gate.sh", 300),
        ("pre_tool", "file_*", "echo ok", 30),
        ("post_tool", "*", "make fmt", 30),
    ]
    assert parse_hooks(None) == () and parse_hooks("x") == ()


class _HookDriver(FakeSandboxDriver):
    def __init__(self, fail_on: str = "") -> None:
        super().__init__()
        self.fail_on = fail_on
        self.hook_runs: list[tuple[str, dict[str, str]]] = []

    async def exec_command(self, session_id: str, command: str, *a: Any, **k: Any) -> ExecResult:
        if command.startswith("hook:"):
            self.hook_runs.append((command, dict(k.get("env") or {})))
            if self.fail_on and command == self.fail_on:
                return ExecResult(3, "policy says no", "", 1)
            return ExecResult(0, "", "", 1)
        return await super().exec_command(session_id, command, *a, **k)


async def _call(
    driver: _HookDriver, hooks: tuple[HookSpec, ...], tool: str, args: dict[str, Any]
) -> str:
    await driver.ensure_workspace(
        WorkspaceSpec(session_id="s1", tenant_org_id="o", tenant_user_id="u", container_image="i")
    )
    await driver.start_sandbox("s1")
    registry = ToolRegistry(driver=driver, session_id="s1")
    token = current_execution_ctx.set(
        ExecutionContext(
            session_id="s1",
            event_queue=asyncio.Queue(),
            parent_model="m",
            policy=replace(TurnPolicy(), hooks=hooks),
        )
    )
    try:
        return await registry.call_tool(tool, args)
    finally:
        current_execution_ctx.reset(token)


async def test_a_failing_pre_tool_hook_blocks_the_call() -> None:
    driver = _HookDriver(fail_on="hook:gate")
    result = await _call(
        driver,
        (HookSpec("pre_tool", "file_write", "hook:gate"),),
        "file_write",
        {"path": "a.txt", "content": "x"},
    )
    assert result.startswith("Blocked by an organization hook")
    assert "policy says no" in result
    assert "a.txt" not in driver._files.get("s1", {})


async def test_a_passing_pre_tool_hook_lets_the_call_through_and_gets_the_context() -> None:
    driver = _HookDriver()
    result = await _call(
        driver,
        (HookSpec("pre_tool", "file_*", "hook:ok"),),
        "file_write",
        {"path": "a.txt", "content": "x; rm -rf /"},
    )
    assert "a.txt" in driver._files["s1"]
    assert not result.startswith("Blocked")
    ((command, env),) = driver.hook_runs
    assert command == "hook:ok"  # arguments are never spliced into the command line
    assert env["ROCKET_TOOL_NAME"] == "file_write"
    assert "rm -rf" in env["ROCKET_TOOL_ARGS"]


async def test_a_failing_post_tool_hook_is_reported_to_the_agent() -> None:
    driver = _HookDriver(fail_on="hook:fmt")
    result = await _call(
        driver,
        (HookSpec("post_tool", "file_write", "hook:fmt"),),
        "file_write",
        {"path": "a.txt", "content": "x"},
    )
    assert "[post_tool hook failed]" in result and "policy says no" in result
    assert "a.txt" in driver._files["s1"], "the tool itself already ran"


async def test_hooks_only_fire_for_matching_tools() -> None:
    driver = _HookDriver()
    await _call(driver, (HookSpec("pre_tool", "bash_exec", "hook:ok"),), "list_dir", {"path": "."})
    assert driver.hook_runs == []
