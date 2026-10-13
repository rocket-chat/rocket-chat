"""
Tool Dispatcher handling parallel tool execution and per-path file mutation serialization.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from agent_core.repeats import repeated_failure_note
from agent_core.tools.registry import ToolRegistry
from agent_core.transcript import INTERRUPTED_TOOL_RESULT
from specifications.interfaces.agent import AgentEvent
from specifications.interfaces.llm import ChatMessage

_tracer = trace.get_tracer("agent_core.dispatcher")

# A turn may fan out several tools at once (parallel reads, subagent delegations). Both
# the fan-out width and each tool's wall-clock budget are bounded so one session cannot
# exhaust the pod or hang a turn forever. The timeout is deliberately generous (matches
# the sandbox lifetime) because a legitimate install/build can run for minutes.
_DEFAULT_MAX_CONCURRENCY = 8
_DEFAULT_TOOL_TIMEOUT_SECONDS = 600


def _env_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw and raw.strip().isdigit() and int(raw) > 0:
        return int(raw)
    return default


def _parse_arguments(raw_args: Any) -> tuple[dict[str, Any], str | None]:
    """Decode model-supplied tool arguments without ever raising."""
    if isinstance(raw_args, dict):
        return raw_args, None
    if not raw_args:
        return {}, None
    try:
        parsed = json.loads(raw_args)
    except (TypeError, ValueError) as err:
        return {}, f"arguments are not valid JSON ({err})"
    if not isinstance(parsed, dict):
        return {}, "arguments must be a JSON object"
    return parsed, None


class ToolDispatcher:
    """Dispatches tool executions in parallel with serialized file locking per path."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        max_concurrency: int | None = None,
        tool_timeout_seconds: int | None = None,
    ) -> None:
        self.registry = registry
        # (session_id, path) -> [lock, holders]. Scoped per session so two sessions editing
        # a same-named file do not serialize each other, and entries are dropped as soon
        # as nobody holds or awaits them, which keeps the map from growing for ever.
        self._file_locks: dict[tuple[str, str], list[Any]] = {}
        self.max_concurrency = max_concurrency or _env_positive_int(
            "ROCKET_TOOL_MAX_CONCURRENCY", _DEFAULT_MAX_CONCURRENCY
        )
        self.tool_timeout_seconds = tool_timeout_seconds or _env_positive_int(
            "ROCKET_TOOL_TIMEOUT_SECONDS", _DEFAULT_TOOL_TIMEOUT_SECONDS
        )
        # Bound concurrent tool executions across the whole process; the per-path file
        # locks below still serialize mutations of a single file.
        self._semaphore = asyncio.Semaphore(self.max_concurrency)

    @asynccontextmanager
    async def file_lock(self, session_id: str, file_path: str) -> AsyncIterator[None]:
        key = (session_id, file_path)
        entry = self._file_locks.get(key)
        if entry is None:
            entry = [asyncio.Lock(), 0]
            self._file_locks[key] = entry
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                self._file_locks.pop(key, None)

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
        args, argument_error = _parse_arguments(raw_args)

        # Acquire per-file lock for mutating tools to prevent concurrency corruption
        file_path = args.get("path")
        needs_lock = tool_name in (
            "file_write",
            "file_edit",
            "apply_patch",
            "apply_diff_file",
        ) and bool(file_path)

        timeout_seconds = self.tool_timeout_seconds
        timed_out = False
        # Tool arguments are deliberately not span attributes: they can carry secrets.
        with _tracer.start_as_current_span(
            "agent.tool", attributes={"rocket.tool": tool_name, "rocket.session_id": session_id}
        ) as span:
            try:
                if argument_error is not None:
                    raise ValueError(argument_error)
                if needs_lock:
                    async with self.file_lock(session_id, str(file_path)):
                        output = await asyncio.wait_for(
                            self.registry.call_tool(tool_name, args), timeout=timeout_seconds
                        )
                else:
                    output = await asyncio.wait_for(
                        self.registry.call_tool(tool_name, args), timeout=timeout_seconds
                    )
                status = "completed"
            except TimeoutError:
                # Distinguish an over-budget tool from a failed one: the trace records both the
                # status and the limit so a hung tool is attributable rather than a generic error.
                output = f"Tool execution timed out after {timeout_seconds}s"
                status = "timeout"
                timed_out = True
                span.set_status(Status(StatusCode.ERROR, status))
            except Exception as err:
                output = f"Tool execution failed: {err}"
                status = "error"
                span.record_exception(err)
                span.set_status(Status(StatusCode.ERROR, str(err)))
            span.set_attribute("rocket.tool_status", status)

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
                    "timed_out": timed_out,
                    "timeout_ms": timeout_seconds * 1000,
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
        ordered = list(tool_calls_raw.values())
        # The assistant message and its results must agree on ids, and providers may omit
        # them, so every call gets a stable id before anything is recorded.
        for tc in ordered:
            tc["id"] = tc.get("id") or f"call_{uuid.uuid4().hex[:12]}"
        messages.append(
            ChatMessage(
                role="assistant",
                tool_calls=[
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in ordered
                ],
            )
        )

        outputs: dict[str, str] = {}

        # Fan out emitted tool calls concurrently, but never more than the bounded
        # semaphore allows; each call carries its own per-tool timeout inside
        # execute_single_tool, so a slow tool cannot hold the whole batch open.
        async def _bounded(tc: dict[str, Any]) -> None:
            async with self._semaphore:
                call_id, _name, output, _duration = await self.execute_single_tool(
                    session_id, tc, event_queue
                )
            outputs[call_id] = output

        try:
            await asyncio.gather(*(_bounded(tc) for tc in ordered))
        finally:
            # Always close every call, even when the batch is cancelled or crashes: an
            # assistant message with unanswered tool calls would make every later model
            # request for this session fail.
            earlier = list(messages)
            for tc in ordered:
                content = outputs.get(tc["id"], INTERRUPTED_TOOL_RESULT)
                if note := repeated_failure_note(earlier, tc["name"], tc["arguments"], content):
                    content = f"{content}\n\n{note}"
                messages.append(ChatMessage(role="tool", content=content, tool_call_id=tc["id"]))
