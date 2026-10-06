"""
Tool Dispatcher handling parallel tool execution and per-path file mutation serialization.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from agent_core.tools.registry import ToolRegistry
from specifications.interfaces.agent import AgentEvent
from specifications.interfaces.llm import ChatMessage


class ToolDispatcher:
    """Dispatches tool executions in parallel with serialized file locking per path."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self._file_locks: dict[str, asyncio.Lock] = {}

    def get_file_lock(self, file_path: str) -> asyncio.Lock:
        if file_path not in self._file_locks:
            self._file_locks[file_path] = asyncio.Lock()
        return self._file_locks[file_path]

    async def execute_single_tool(
        self,
        session_id: str,
        tc: dict[str, Any],
        event_queue: asyncio.Queue[AgentEvent | None],
    ) -> tuple[str, str, str, int]:
        call_id = tc.get("id") or str(uuid.uuid4().hex[:8])
        tool_name = tc.get("name") or "unknown"
        raw_args = tc.get("arguments") or "{}"

        await event_queue.put(
            AgentEvent(
                event_type="tool_call",
                session_id=session_id,
                payload={"id": call_id, "name": tool_name, "arguments": raw_args},
            )
        )

        start_time = time.monotonic()
        args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args else {}

        # Acquire per-file lock for mutating tools to prevent concurrency corruption
        file_path = args.get("path")
        lock = None
        if tool_name in ("file_write", "file_edit", "apply_patch") and file_path:
            lock = self.get_file_lock(file_path)

        try:
            if lock:
                async with lock:
                    output = await self.registry.call_tool(tool_name, args)
            else:
                output = await self.registry.call_tool(tool_name, args)
            status = "completed"
        except Exception as err:
            output = f"Tool execution failed: {err}"
            status = "error"

        duration_ms = max(1, int((time.monotonic() - start_time) * 1000))

        await event_queue.put(
            AgentEvent(
                event_type="tool_completed",
                session_id=session_id,
                payload={
                    "id": call_id,
                    "name": tool_name,
                    "arguments": raw_args,
                    "result": output,
                    "status": status,
                    "duration_ms": duration_ms,
                },
            )
        )
        return call_id, tool_name, output, duration_ms

    async def execute_tool_calls(
        self,
        session_id: str,
        tool_calls_raw: dict[int, dict[str, Any]],
        event_queue: asyncio.Queue[AgentEvent | None],
        messages: list[ChatMessage],
    ) -> None:
        formatted_calls = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": tc["arguments"]},
            }
            for tc in tool_calls_raw.values()
        ]
        messages.append(ChatMessage(role="assistant", tool_calls=formatted_calls))

        # Launch all emitted tool calls concurrently (parallel subagents / tools)
        tasks = [
            self.execute_single_tool(session_id, tc, event_queue) for tc in tool_calls_raw.values()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        for call_id, _tool_name, output, _duration_ms in results:
            messages.append(
                ChatMessage(
                    role="tool",
                    content=output,
                    tool_call_id=call_id,
                )
            )
