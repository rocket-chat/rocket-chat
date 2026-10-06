"""Unit and integration tests for parallel tool execution, subagent delegation, and write-only MCP secret management."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.subagents import (
    SubagentRunner,
)
from agent_core.tools.registry import ToolRegistry
from api.main import app
from config_engine.crypto import CredentialCipher
from httpx import ASGITransport, AsyncClient

from specifications.interfaces.agent import (
    AgentEvent,
    SubagentConfig,
    SubagentTaskRequest,
)
from specifications.interfaces.llm import (
    ChatMessage,
    ModelRequest,
    StreamChunk,
)


@pytest.fixture
def cipher() -> CredentialCipher:
    return CredentialCipher(secret_key="unit-test-secret-key-32-bytes-long!")


class TestSubagentRunner:
    """Verifies autonomous specialist subagent execution and recursion limits."""

    @pytest.mark.asyncio
    async def test_subagent_anti_recursion(self) -> None:
        """Ensures subagents cannot call delegate_subagent or delegate_subagents_parallel."""
        mock_gateway = MagicMock()
        registry = ToolRegistry()

        recorded_requests: list[ModelRequest] = []

        async def _mock_stream(req: ModelRequest) -> AsyncIterator[StreamChunk]:
            recorded_requests.append(req)
            yield StreamChunk(
                text_delta="Audit complete: No vulnerabilities found.", is_finished=True
            )

        mock_gateway.chat_stream = _mock_stream

        runner = SubagentRunner(gateway=mock_gateway, registry=registry)
        event_queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

        result = await runner.run(
            request=SubagentTaskRequest(
                role="security_auditor",
                task="Scan for SQL injections",
                context="Checked app/routes.py",
            ),
            session_id="test-session-1",
            event_queue=event_queue,
        )

        assert result.status == "completed"
        assert "Audit complete" in result.summary

        # Verify that tools given to subagent do NOT contain delegate_subagent
        assert len(recorded_requests) >= 1
        sub_tool_names = [t.name for t in (recorded_requests[0].tools or [])]
        assert "delegate_subagent" not in sub_tool_names
        assert "file_read" in sub_tool_names

        # Verify emitted events
        events: list[AgentEvent] = []
        while not event_queue.empty():
            ev = event_queue.get_nowait()
            if ev:
                events.append(ev)

        ev_types = [e.event_type for e in events]
        assert "subagent_started" in ev_types
        assert "subagent_completed" in ev_types

    @pytest.mark.asyncio
    async def test_subagent_turn_limit_cap(self) -> None:
        """Verifies subagents stop after max_turns even if LLM keeps emitting tool calls."""
        mock_gateway = MagicMock()
        registry = ToolRegistry()
        registry.call_tool = AsyncMock(return_value="bash output")

        call_count = 0

        async def _mock_stream(req: ModelRequest) -> AsyncIterator[StreamChunk]:
            nonlocal call_count
            call_count += 1
            # Continually request bash_exec
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": f"call_{call_count}",
                    "function": {
                        "name": "bash_exec",
                        "arguments": json.dumps({"command": "ls"}),
                    },
                },
                is_finished=True,
            )

        mock_gateway.chat_stream = _mock_stream

        custom_config = SubagentConfig(
            id="tight_runner",
            name="Tight Runner",
            role_title="Bounded Runner",
            description="Tests max turns cap",
            system_prompt="Execute turns",
            max_turns=2,  # Strict cap of 2 turns
            whitelisted_tools=["bash_exec"],
            temperature=0.0,
        )

        runner = SubagentRunner(
            gateway=mock_gateway,
            registry=registry,
            configs={"tight_runner": custom_config},
        )

        result = await runner.run(
            request=SubagentTaskRequest(
                role="tight_runner",
                task="Run tests indefinitely",
            ),
            session_id="test-session-2",
        )

        assert result.status == "completed"
        assert call_count == 2
        assert result.turns_taken == 2

    @pytest.mark.asyncio
    async def test_subagent_inherits_parent_model(self) -> None:
        """Verifies subagents inherit the parent chat active model when no role override is defined."""
        mock_gateway = MagicMock()
        registry = ToolRegistry()

        recorded_requests: list[ModelRequest] = []

        async def _mock_stream(req: ModelRequest) -> AsyncIterator[StreamChunk]:
            recorded_requests.append(req)
            yield StreamChunk(
                text_delta="Research synthesis on parent model inheritance.",
                is_finished=True,
            )

        mock_gateway.chat_stream = _mock_stream

        runner = SubagentRunner(gateway=mock_gateway, registry=registry)
        parent_model_choice = "openrouter/anthropic/claude-3.7-sonnet"

        result = await runner.run(
            request=SubagentTaskRequest(
                role="researcher",
                task="Analyze architecture",
            ),
            session_id="test-session-inherit",
            default_model=parent_model_choice,
        )

        assert result.status == "completed"
        assert len(recorded_requests) >= 1
        assert recorded_requests[0].model == parent_model_choice


class TestParallelToolExecutionAndLocking:
    """Verifies parallel tool execution with per-path file mutation serialization."""

    @pytest.mark.asyncio
    async def test_parallel_tool_speedup(self) -> None:
        """Verifies independent tool calls run concurrently rather than sequentially."""
        registry = ToolRegistry()
        mock_gateway = MagicMock()

        # Mock call_tool that sleeps 0.1s
        async def slow_reader(name: str, arguments: dict) -> str:
            await asyncio.sleep(0.1)
            return f"content of {arguments.get('path')}"

        registry.call_tool = slow_reader

        orchestrator = AsyncReActOrchestrator(
            gateway=mock_gateway,
            registry=registry,
        )

        tool_calls_raw = {
            0: {"id": "1", "name": "file_read", "arguments": json.dumps({"path": "a.txt"})},
            1: {"id": "2", "name": "file_read", "arguments": json.dumps({"path": "b.txt"})},
            2: {"id": "3", "name": "file_read", "arguments": json.dumps({"path": "c.txt"})},
        }

        event_queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        messages: list[ChatMessage] = []

        t0 = time.monotonic()
        await orchestrator._execute_tool_calls("session-1", tool_calls_raw, event_queue, messages)
        elapsed = time.monotonic() - t0

        # If run sequentially, 3 * 0.1s = ~0.30s. If concurrent, ~0.10s-0.18s.
        assert elapsed < 0.25, f"Expected concurrent execution under 0.25s, got {elapsed:.3f}s"
        # 1 assistant call message + 3 tool result messages
        assert len(messages) == 4

    @pytest.mark.asyncio
    async def test_file_lock_serializes_identical_path_writes(self) -> None:
        """Verifies concurrent writes to the same path are locked and serialized safely."""
        registry = ToolRegistry()
        mock_gateway = MagicMock()

        execution_order: list[str] = []

        async def locked_writer(name: str, arguments: dict) -> str:
            content = arguments.get("content", "")
            path = arguments.get("path", "")
            execution_order.append(f"start-{content}")
            await asyncio.sleep(0.05)
            execution_order.append(f"end-{content}")
            return f"wrote {content} to {path}"

        registry.call_tool = locked_writer

        orchestrator = AsyncReActOrchestrator(
            gateway=mock_gateway,
            registry=registry,
        )

        tool_calls_raw = {
            0: {
                "id": "w1",
                "name": "file_write",
                "arguments": json.dumps({"path": "same.py", "content": "first"}),
            },
            1: {
                "id": "w2",
                "name": "file_write",
                "arguments": json.dumps({"path": "same.py", "content": "second"}),
            },
        }

        event_queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        messages: list[ChatMessage] = []

        await orchestrator._execute_tool_calls("session-2", tool_calls_raw, event_queue, messages)

        # Due to per-path file locking on same.py, the first write must end before the second starts
        assert execution_order == [
            "start-first",
            "end-first",
            "start-second",
            "end-second",
        ]


class TestMcpWriteOnlySecretVault:
    """Verifies write-only encrypted storage for MCP servers and safe secret masking."""

    @pytest.mark.asyncio
    async def test_mcp_secrets_are_write_only_and_masked(self) -> None:
        """Tests that API keys and header secrets are encrypted and NEVER returned cleartext."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer dev_token"},
        ) as client:
            # 1. Register a custom MCP server with sensitive API key and headers
            mcp_payload = {
                "name": "Production Sentry",
                "transport": "http",
                "endpoint_or_command": "https://sentry.example.com/mcp",
                "guidance": "Use for inspecting issue stack traces.",
                "auth": {
                    "auth_type": "api_key",
                    "api_key": "sntrys_99887766554433221100aabbccddeeff",
                    "header_name": "Authorization",
                    "header_prefix": "Bearer",
                    "headers": [
                        {
                            "name": "X-Custom-Secret-Header",
                            "value": "super-confidential-secret-token-xyz",
                            "is_secret": True,
                        },
                        {
                            "name": "X-Client-Id",
                            "value": "public-client-123",
                            "is_secret": False,
                        },
                    ],
                },
            }

            create_resp = await client.post("/v1/mcp/servers", json=mcp_payload)
            assert create_resp.status_code == 200
            data = create_resp.json()

            # Confirm write-only security guarantee:
            # Cleartext secret values MUST NOT appear in response
            assert "sntrys_99887766554433221100aabbccddeeff" not in json.dumps(data)
            assert "super-confidential-secret-token-xyz" not in json.dumps(data)

            # Metadata and fingerprints must be present
            assert data["has_api_key"] is True
            assert data["api_key_fingerprint"] is not None
            assert (
                "eeff" in data["api_key_fingerprint"]
            )  # last 4 chars preserved for identification

            server_id = data["id"]

            # 2. Query GET /v1/mcp/servers - verify all list entries remain masked
            list_resp = await client.get("/v1/mcp/servers")
            assert list_resp.status_code == 200
            all_servers = list_resp.json()
            assert any(s["id"] == server_id for s in all_servers)

            # Verify no secret leaked in GET response
            dumped_all = json.dumps(all_servers)
            assert "sntrys_99887766554433221100aabbccddeeff" not in dumped_all
            assert "super-confidential-secret-token-xyz" not in dumped_all

            # 3. Probe the server
            probe_resp = await client.post(f"/v1/mcp/servers/{server_id}/probe")
            assert probe_resp.status_code == 200
            probe_data = probe_resp.json()
            assert probe_data["status"] == "connected"

            # 4. Clean up - DELETE custom server
            del_resp = await client.delete(f"/v1/mcp/servers/{server_id}")
            assert del_resp.status_code == 200


class TestSubagentRestEndpoints:
    """Verifies subagent configuration listing and updating REST endpoints."""

    @pytest.mark.asyncio
    async def test_subagent_crud_flow(self) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer dev_token"},
        ) as client:
            # 1. GET /v1/subagents
            get_resp = await client.get("/v1/subagents")
            assert get_resp.status_code == 200
            subagents = get_resp.json()
            assert len(subagents) >= 3

            sec_agent = next(s for s in subagents if s["id"] == "security_auditor")
            assert "Security" in sec_agent["name"]
            assert sec_agent["max_turns"] == 4

            # 2. PUT /v1/subagents/security_auditor with updated prompt and turn cap
            sec_agent["max_turns"] = 6
            sec_agent["system_prompt"] = (
                "Updated security audit system prompt with OWASP Top 10 focus."
            )

            put_resp = await client.put("/v1/subagents/security_auditor", json=sec_agent)
            assert put_resp.status_code == 200
            updated = put_resp.json()
            assert "OWASP Top 10" in updated["system_prompt"]
