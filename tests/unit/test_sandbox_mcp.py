"""The sandbox MCP: sandboxes as sessions, the platform's tools proxied, and never a model call.

The service runs against the real orchestrator dispatcher and tool registry, the in-memory
session store and the fake sandbox driver; the model is a ``ScriptedModel`` with an empty script
that records every request it gets, so "no model call" is observed, not assumed.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.db.session import current_tenant_org_id
from api.events import AsyncIOEventBus
from api.sandbox_mcp.activity import latest_activity
from api.sandbox_mcp.calls import LAST_CALL_AT
from api.sandbox_mcp.server import build_sandbox_mcp
from api.sandbox_mcp.service import SandboxMcpService
from api.services.compaction import InMemoryCompactionStore
from api.services.sandbox_catalogue import SandboxCatalogue
from api.services.settings import save_org_config
from api.services.transcript import InMemoryTranscriptStore
from api.services.workspace_release import RELEASED_AFTER_DAYS, RELEASED_AT
from api.store import InMemorySessionStore
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import SandboxStatus
from specifications.interfaces.sandbox_mcp import (
    SANDBOX_ID_ARGUMENT,
    SANDBOX_MCP_TOOLS,
    McpCaller,
    SandboxMcpError,
)
from specifications.interfaces.session import SessionRecord, SessionSource
from tests.turn_harness import ScriptedModel


class Platform:
    """An application state with the collaborators the MCP reads."""

    def __init__(self) -> None:
        self.model = ScriptedModel()
        self.driver = FakeSandboxDriver()
        self.store = InMemorySessionStore()
        registry = ToolRegistry(driver=self.driver)
        orchestrator = AsyncReActOrchestrator(
            gateway=self.model,
            registry=registry,
            transcript_store=InMemoryTranscriptStore(),
            compaction_store=InMemoryCompactionStore(),
        )
        self.state = SimpleNamespace(
            session_store=self.store,
            sandbox_driver=self.driver,
            event_bus=AsyncIOEventBus(),
            orchestrator=orchestrator,
            tool_registry=registry,
        )
        self.service = SandboxMcpService(self.state)


@pytest.fixture
def org() -> Iterator[str]:
    org_id = f"org_mcp_{uuid.uuid4().hex[:8]}"
    token = current_tenant_org_id.set(org_id)
    yield org_id
    current_tenant_org_id.reset(token)


async def _enabled(org: str, **settings: object) -> Platform:
    await save_org_config(None, org, "sandboxes", {"mcp_enabled": True, **settings}, [])
    return Platform()


def _member(org: str, user: str = "alice") -> McpCaller:
    return McpCaller(org_id=org, user_id=user, roles=("member",))


@pytest.mark.setting("sandboxes.mcp_enabled")
async def test_the_mcp_is_refused_until_an_administrator_turns_it_on(org: str) -> None:
    platform = Platform()

    with pytest.raises(SandboxMcpError, match="turned off"):
        await platform.service.sandbox_types(_member(org))
    with pytest.raises(SandboxMcpError, match="turned off"):
        await platform.service.open_sandbox(_member(org), None, None)

    await save_org_config(None, org, "sandboxes", {"mcp_enabled": True}, [])
    assert await platform.service.sandbox_types(_member(org))


async def test_a_sandbox_is_a_session_of_the_chosen_type(org: str) -> None:
    platform = await _enabled(org)
    entry = await SandboxCatalogue(None).create(
        org,
        name="Python",
        image="img/python:3.12",
        env=[{"name": "SERVICE_URL", "value": "https://svc.internal", "secret": False}],
    )
    types = await platform.service.sandbox_types(_member(org))
    listed = next(t for t in types if t.id == entry.id)
    assert listed.variables == ("SERVICE_URL",), "names only: a value never leaves the platform"

    opened = await platform.service.open_sandbox(_member(org), entry.id, "fix the parser")

    record = await platform.store.get_session(opened.sandbox_id)
    assert record is not None
    assert record.source == SessionSource.MCP
    assert record.tenant_user_id == "alice"
    assert record.container_image == "img/python:3.12"
    assert record.metadata["sandbox_id"] == entry.id
    assert record.title == "fix the parser"
    assert opened.cockpit_path == f"/chat/{opened.sandbox_id}"
    assert [s.sandbox_id for s in await platform.service.list_sandboxes(_member(org))] == [
        opened.sandbox_id
    ]
    with pytest.raises(SandboxMcpError, match="no sandbox type"):
        await platform.service.open_sandbox(_member(org), "sbx_missing", None)


async def test_a_call_starts_the_sandbox_and_runs_the_tool_without_any_model_call(
    org: str,
) -> None:
    platform = await _enabled(org)
    entry = await SandboxCatalogue(None).create(
        org,
        name="Service",
        image="img/service:1",
        env=[{"name": "SERVICE_URL", "value": "https://svc.internal", "secret": False}],
    )
    opened = await platform.service.open_sandbox(_member(org), entry.id, None)

    outcome = await platform.service.call_tool(
        _member(org), opened.sandbox_id, "bash_exec", {"command": "echo ready", "cwd": "."}
    )

    assert not outcome.is_error
    assert await platform.driver.get_status(opened.sandbox_id) == SandboxStatus.RUNNING
    assert any("echo ready" in command for _, command in platform.driver.exec_calls)
    ran_with = next(
        env
        for (_, command), env in zip(
            platform.driver.exec_calls, platform.driver.exec_envs, strict=False
        )
        if "echo ready" in command
    )
    assert ran_with.get("SERVICE_URL") == "https://svc.internal", "the type's variables apply"
    assert platform.model.requests == [], "a sandbox MCP call never reaches a model"
    record = await platform.store.get_session(opened.sandbox_id)
    assert record is not None and record.metadata[LAST_CALL_AT] > 0


async def test_a_paused_sandbox_resumes_on_the_next_call(org: str) -> None:
    platform = await _enabled(org)
    member = _member(org)
    opened = await platform.service.open_sandbox(member, None, None)
    await platform.service.call_tool(
        member, opened.sandbox_id, "file_write", {"path": "notes.txt", "content": "kept"}
    )

    await platform.driver.hibernate_sandbox(opened.sandbox_id)
    outcome = await platform.service.call_tool(
        member, opened.sandbox_id, "file_read", {"path": "notes.txt"}
    )

    assert await platform.driver.get_status(opened.sandbox_id) == SandboxStatus.RUNNING
    assert "kept" in outcome.text, "the workspace survives the pause"
    assert platform.model.requests == []


async def test_the_list_says_when_a_sandbox_is_paused(org: str) -> None:
    platform = await _enabled(org)
    opened = await platform.service.open_sandbox(_member(org), None, None)
    await platform.service.call_tool(
        _member(org), opened.sandbox_id, "file_write", {"path": "a.txt", "content": "x"}
    )

    await platform.driver.hibernate_sandbox(opened.sandbox_id)

    (listed,) = await platform.service.list_sandboxes(_member(org))
    assert listed.status == SandboxStatus.HIBERNATED.value, "the driver's state, not the record's"


async def test_the_first_call_after_a_release_says_the_workspace_is_gone(org: str) -> None:
    platform = await _enabled(org)
    opened = await platform.service.open_sandbox(_member(org), None, None)

    def released(record: SessionRecord) -> None:
        record.metadata = {**record.metadata, RELEASED_AT: 1_700_000_000, RELEASED_AFTER_DAYS: 7}

    await platform.store.mutate_session(opened.sandbox_id, org, released)
    first = await platform.service.call_tool(_member(org), opened.sandbox_id, "env_list", {})
    second = await platform.service.call_tool(_member(org), opened.sandbox_id, "env_list", {})

    assert first.text.startswith("This session's workspace was released after 7 idle day(s)")
    assert "released" not in second.text, "said once"


async def test_only_the_owner_reaches_a_sandbox_and_only_an_mcp_one(org: str) -> None:
    platform = await _enabled(org)
    opened = await platform.service.open_sandbox(_member(org), None, None)
    await platform.store.create_session(
        SessionRecord(session_id="session_web", tenant_org_id=org, tenant_user_id="alice", title="")
    )

    with pytest.raises(SandboxMcpError, match="No sandbox"):
        await platform.service.call_tool(_member(org, "mallory"), opened.sandbox_id, "env_list", {})
    with pytest.raises(SandboxMcpError, match="No sandbox"):
        await platform.service.close_sandbox(_member(org, "mallory"), opened.sandbox_id)
    with pytest.raises(SandboxMcpError, match="No sandbox"):
        await platform.service.call_tool(_member(org), "session_web", "env_list", {})
    other_org = McpCaller(org_id=f"{org}_other", user_id="alice", roles=("member",))
    await save_org_config(None, other_org.org_id, "sandboxes", {"mcp_enabled": True}, [])
    with pytest.raises(SandboxMcpError, match="No sandbox"):
        await platform.service.call_tool(other_org, opened.sandbox_id, "env_list", {})
    assert await platform.service.list_sandboxes(_member(org, "mallory")) == []


async def test_a_viewer_cannot_open_a_sandbox(org: str) -> None:
    platform = await _enabled(org)
    viewer = McpCaller(org_id=org, user_id="victor", roles=("viewer",))

    with pytest.raises(SandboxMcpError, match="viewer"):
        await platform.service.open_sandbox(viewer, None, None)


@pytest.mark.parametrize("name", ["code_search", "ask_question", "delegate_subagent", "recall"])
async def test_a_tool_outside_the_mcp_list_is_refused(org: str, name: str) -> None:
    platform = await _enabled(org)
    opened = await platform.service.open_sandbox(_member(org), None, None)

    with pytest.raises(SandboxMcpError, match="not a sandbox MCP tool"):
        await platform.service.call_tool(_member(org), opened.sandbox_id, name, {})
    assert platform.driver.exec_calls == []


async def test_a_failed_tool_is_reported_as_an_error(org: str) -> None:
    platform = await _enabled(org)
    opened = await platform.service.open_sandbox(_member(org), None, None)

    outcome = await platform.service.call_tool(
        _member(org), opened.sandbox_id, "file_read", {"path": "missing.txt"}
    )

    assert outcome.is_error, outcome.text


async def test_a_call_is_refused_while_an_agent_turn_runs_in_the_session(org: str) -> None:
    platform = await _enabled(org)
    opened = await platform.service.open_sandbox(_member(org), None, None)

    def processing(record: SessionRecord) -> None:
        record.is_processing = True

    await platform.store.mutate_session(opened.sandbox_id, org, processing)

    with pytest.raises(SandboxMcpError, match="agent turn is running"):
        await platform.service.call_tool(
            _member(org), opened.sandbox_id, "bash_exec", {"command": "true", "cwd": "."}
        )


@pytest.mark.setting("sandboxes.mcp_max_open_sandboxes")
async def test_a_member_keeps_at_most_the_configured_number_of_sandboxes(org: str) -> None:
    platform = await _enabled(org, mcp_max_open_sandboxes=1)
    first = await platform.service.open_sandbox(_member(org), None, None)

    with pytest.raises(SandboxMcpError, match="already have 1"):
        await platform.service.open_sandbox(_member(org), None, None)
    await platform.service.open_sandbox(_member(org, "bob"), None, None)

    assert await platform.service.close_sandbox(_member(org), first.sandbox_id)
    assert await platform.service.open_sandbox(_member(org), None, None)


async def test_closing_deletes_the_session_and_its_workspace(org: str) -> None:
    platform = await _enabled(org)
    opened = await platform.service.open_sandbox(_member(org), None, None)
    await platform.service.call_tool(
        _member(org), opened.sandbox_id, "file_write", {"path": "a.txt", "content": "x"}
    )

    assert await platform.service.close_sandbox(_member(org), opened.sandbox_id)

    assert await platform.store.get_session(opened.sandbox_id) is None
    assert await platform.driver.get_status(opened.sandbox_id) != SandboxStatus.RUNNING
    assert await platform.service.list_sandboxes(_member(org)) == []


async def test_the_served_tools_are_the_lifecycle_and_the_listed_platform_tools() -> None:
    server = build_sandbox_mcp(SandboxMcpService(SimpleNamespace()))

    tools = {tool.name: tool for tool in await server.list_tools()}

    lifecycle = {"sandbox_types", "sandbox_open", "sandbox_list", "sandbox_close"}
    assert set(tools) == lifecycle | set(SANDBOX_MCP_TOOLS)
    registry = {d.name: d for d in ToolRegistry().get_tool_definitions()}
    for name in SANDBOX_MCP_TOOLS:
        schema = tools[name].parameters
        assert schema["required"][0] == SANDBOX_ID_ARGUMENT
        own = registry[name].parameters_json_schema.get("properties", {})
        assert set(schema["properties"]) == set(own) | {SANDBOX_ID_ARGUMENT}


def test_the_reaper_sees_the_latest_activity_of_turns_and_mcp_calls() -> None:
    merged = latest_activity({"a": 10.0, "b": 50.0}, {"a": 40.0, "c": 5.0})

    assert merged == {"a": 40.0, "b": 50.0, "c": 5.0}
    assert latest_activity(None, None) is None
    assert latest_activity(None, {"a": 1.0}) == {"a": 1.0}
