"""What the model is told about its environment (system prompt)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.events import AsyncIOEventBus
from api.store import InMemorySessionStore
from api.turns.runner import execute_turn_background
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.llm import LLMGatewayProtocol, StreamChunk
from specifications.interfaces.session import SessionRecord


class _Recorder(LLMGatewayProtocol):
    def __init__(self) -> None:
        self.system_prompts: list[str] = []
        self.tool_names: list[str] = []

    async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
        self.system_prompts.append(
            "\n".join(str(m.content) for m in request.messages if m.role == "system")
        )
        # Later auxiliary calls (title, summary) carry no tools; keep the turn's own.
        self.tool_names = self.tool_names or [t.name for t in request.tools or []]
        yield StreamChunk(text_delta="ok")

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


async def _system_prompt_for(session: SessionRecord) -> tuple[str, list[str]]:
    gateway = _Recorder()
    driver = FakeSandboxDriver()
    registry = ToolRegistry(driver=driver, session_id=session.session_id)
    store = InMemorySessionStore()
    await store.create_session(session)
    await execute_turn_background(
        session_id=session.session_id,
        prompt="hello",
        orchestrator=AsyncReActOrchestrator(gateway=gateway, registry=registry),
        event_bus=AsyncIOEventBus(),
        driver=driver,
        registry=registry,
        store=store,
    )
    return gateway.system_prompts[0], gateway.tool_names


def _session(**extra: Any) -> SessionRecord:
    return SessionRecord(
        session_id="session_ctx", tenant_org_id="o", tenant_user_id="u", title="t", **extra
    )


async def test_the_agent_is_told_the_sandbox_starts_blank_and_how_to_find_and_get_code() -> None:
    prompt, tools = await _system_prompt_for(_session())

    assert "starts BLANK" in prompt
    assert "Many tasks need no sandbox and no repository" in prompt
    # Where to look for the repository, cheapest first, and how to fetch it.
    for expected in (
        "`kb_search`",
        "skills",
        "`github_find_repositories`",
        "`github_read_file`",
        "`github_issues`",
        "`mcp__github__*`",
        "`clone_repository`",
    ):
        assert expected in prompt, expected
    assert {
        "clone_repository",
        "github_find_repositories",
        "github_read_file",
        "github_issues",
    } <= set(tools)


async def test_asking_the_user_is_presented_as_a_blocking_last_resort() -> None:
    prompt, _ = await _system_prompt_for(_session())

    assert "`ask_question` BLOCKS the whole turn" in prompt
    assert "last resort" in prompt
    assert "state your assumption" in prompt


async def test_the_agent_is_not_invited_to_hunt_for_credentials() -> None:
    prompt, _ = await _system_prompt_for(_session())

    assert "Do not search the environment for tokens" in prompt


async def test_a_session_started_from_a_repository_says_it_is_already_there() -> None:
    prompt, _ = await _system_prompt_for(_session(git_repo="https://github.com/acme/api.git"))

    assert "Active Repository: `https://github.com/acme/api.git`" in prompt
