"""
Tool Dispatcher handling parallel tool execution and per-path file mutation serialization.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from agent_core.context import current_execution_ctx
from agent_core.repeats import repeated_failure_note
from agent_core.tool_slot import ToolSlot, current_tool_slot
from agent_core.tools.offload import DEFAULT_OFFLOAD_THRESHOLD, offload_tool_output
from agent_core.tools.registry import ToolRegistry
from agent_core.transcript import INTERRUPTED_TOOL_RESULT
from specifications.interfaces.agent import AgentEvent
from specifications.interfaces.llm import ChatMessage

_tracer = trace.get_tracer("agent_core.dispatcher")

# A turn may fan out several tools at once (parallel reads, subagent delegations). The fan-out
# of one session, the tools running in the whole process and each tool's working time are all
# bounded, so one session cannot starve the others, exhaust the pod or hang a turn forever. The
# timeout is deliberately generous (matches the sandbox lifetime) because a legitimate
# install/build can run for minutes. Time spent waiting for a person is not working time.
_DEFAULT_MAX_CONCURRENCY = 8
_DEFAULT_PROCESS_CONCURRENCY = 64
_DEFAULT_TOOL_TIMEOUT_SECONDS = 600
# How often the watchdog looks at a call that is waiting for a person.
_WATCH_INTERVAL_SECONDS = 1.0


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


# Edits of one file are serialised: concurrent read-modify-write would lose one of them.
_PATH_LOCKED_TOOLS = frozenset({"file_write", "file_edit", "apply_patch", "apply_diff_file"})


class ToolDispatcher:
    """Dispatches tool executions in parallel with serialized file locking per path."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        max_concurrency: int | None = None,
        tool_timeout_seconds: int | None = None,
        process_concurrency: int | None = None,
        redactor: Callable[[str], tuple[str, int] | str] | None = None,
    ) -> None:
        self.registry = registry
        self.redactor = redactor
        # (session_id, path) -> [lock, holders]. Scoped per session so two sessions editing
        # a same-named file do not serialize each other, and entries are dropped as soon
        # as nobody holds or awaits them, which keeps the map from growing for ever.
        self._file_locks: dict[tuple[str, str], list[Any]] = {}
        # Per session: how many of one session's calls run at once.
        self.max_concurrency = max_concurrency or _env_positive_int(
            "ROCKET_TOOL_MAX_CONCURRENCY", _DEFAULT_MAX_CONCURRENCY
        )
        self.process_concurrency = process_concurrency or _env_positive_int(
            "ROCKET_TOOL_PROCESS_CONCURRENCY", _DEFAULT_PROCESS_CONCURRENCY
        )
        self.tool_timeout_seconds = tool_timeout_seconds or _env_positive_int(
            "ROCKET_TOOL_TIMEOUT_SECONDS", _DEFAULT_TOOL_TIMEOUT_SECONDS
        )
        self._process_limit = asyncio.Semaphore(self.process_concurrency)
        # session_id -> [semaphore, users]; dropped when no call of the session holds or awaits it.
        self._session_limits: dict[str, list[Any]] = {}

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

    @asynccontextmanager
    async def _slot(self, session_id: str) -> AsyncIterator[ToolSlot]:
        """A slot under the session's and the process's limits, released on exit."""
        entry = self._session_limits.get(session_id)
        if entry is None:
            entry = [asyncio.Semaphore(self.max_concurrency), 0]
            self._session_limits[session_id] = entry
        entry[1] += 1
        slot = ToolSlot([entry[0], self._process_limit], self.tool_timeout_seconds)
        try:
            await slot.acquire()
            yield slot
        finally:
            slot.release()
            entry[1] -= 1
            if entry[1] == 0:
                self._session_limits.pop(session_id, None)

    async def _within_budget(self, slot: ToolSlot, name: str, args: dict[str, Any]) -> str:
        """Run the tool, failing it once its working time (waits for a person excluded) is spent."""
        token = current_tool_slot.set(slot)
        try:
            task = asyncio.ensure_future(self.registry.call_tool(name, args))
        finally:
            current_tool_slot.reset(token)
        try:
            while True:
                remaining = slot.remaining()
                if remaining <= 0 and not slot.waiting:
                    task.cancel()
                    await asyncio.wait({task})
                    raise TimeoutError
                timeout = _WATCH_INTERVAL_SECONDS if slot.waiting else remaining
                done, _ = await asyncio.wait({task}, timeout=min(timeout, _WATCH_INTERVAL_SECONDS))
                if done:
                    return task.result()
        except asyncio.CancelledError:
            task.cancel()
            raise

    async def _run_tool(self, session_id: str, name: str, args: dict[str, Any]) -> str:
        async with self._slot(session_id) as slot:
            return await self._within_budget(slot, name, args)

    async def run_guarded(self, session_id: str, name: str, args: dict[str, Any]) -> str:
        """Run one tool under the session's limits, its time budget and, for an edit, the
        file's lock. Raises ``TimeoutError`` when the budget is spent."""
        file_path = args.get("path")
        if name in _PATH_LOCKED_TOOLS and file_path:
            async with self.file_lock(session_id, str(file_path)):
                return await self._run_tool(session_id, name, args)
        return await self._run_tool(session_id, name, args)

    async def execute_single_tool(
        self,
        session_id: str,
        tc: dict[str, Any],
        event_queue: asyncio.Queue[AgentEvent | None],
    ) -> tuple[str, str, str, int]:
        call_id = tc.get("id") or str(uuid.uuid4().hex[:8])
        tool_name = tc.get("name") or "unknown"
        raw_args = tc.get("arguments") or "{}"

        redacted_args = raw_args
        ctx = current_execution_ctx.get()
        redactor = self.redactor or (
            ctx.callbacks.redact_tool_output if ctx and ctx.callbacks else None
        )
        if redactor is not None and isinstance(raw_args, str):
            res = redactor(raw_args)
            redacted_args = res[0] if isinstance(res, tuple) else str(res)

        await event_queue.put(
            AgentEvent(
                event_type="tool_call",
                session_id=session_id,
                payload={"id": call_id, "name": tool_name, "arguments": redacted_args},
            )
        )

        start_time = time.monotonic()
        args, argument_error = _parse_arguments(raw_args)

        timeout_seconds = self.tool_timeout_seconds
        timed_out = False
        # Tool arguments are deliberately not span attributes: they can carry secrets.
        with _tracer.start_as_current_span(
            "agent.tool", attributes={"rocket.tool": tool_name, "rocket.session_id": session_id}
        ) as span:
            try:
                if argument_error is not None:
                    raise ValueError(argument_error)
                output = await self.run_guarded(session_id, tool_name, args)
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

        redacted_count = 0
        ctx = current_execution_ctx.get()
        redactor = self.redactor or (
            ctx.callbacks.redact_tool_output if ctx and ctx.callbacks else None
        )
        if redactor is not None and isinstance(output, str):
            res = redactor(output)
            if isinstance(res, tuple):
                output, redacted_count = res
            else:
                output = str(res)

        if isinstance(output, str) and tool_name not in ("file_read", "read_file"):
            driver = getattr(self.registry, "driver", None)
            threshold = (
                ctx.policy.output_offload_threshold
                if ctx and hasattr(ctx.policy, "output_offload_threshold")
                else DEFAULT_OFFLOAD_THRESHOLD
            )
            turn_idx = getattr(ctx, "turn_index", 1) if ctx else 1
            output = await offload_tool_output(
                output,
                session_id=session_id,
                call_id=call_id,
                threshold=threshold,
                driver=driver,
                turn_index=turn_idx,
            )

        payload: dict[str, Any] = {
            "id": call_id,
            "name": tool_name,
            "arguments": raw_args,
            "result": output,
            "status": status,
            "duration_ms": duration_ms,
            "timed_out": timed_out,
            "timeout_ms": timeout_seconds * 1000,
        }
        if redacted_count:
            payload["redacted"] = redacted_count

        await event_queue.put(
            AgentEvent(
                event_type="tool_completed",
                session_id=session_id,
                payload=payload,
            )
        )
        return call_id, tool_name, output, duration_ms

    async def execute_tool_calls(
        self,
        session_id: str,
        tool_calls_raw: dict[int, dict[str, Any]],
        event_queue: asyncio.Queue[AgentEvent | None],
        messages: list[ChatMessage],
        *,
        text: str = "",
        reasoning: str = "",
        reasoning_blocks: list[dict[str, Any]] | None = None,
    ) -> None:
        ordered = list(tool_calls_raw.values())
        # The assistant message and its results must agree on ids, and providers may omit
        # them, so every call gets a stable id before anything is recorded.
        for tc in ordered:
            tc["id"] = tc.get("id") or f"call_{uuid.uuid4().hex[:12]}"
        messages.append(
            ChatMessage(
                role="assistant",
                # What the model said and thought before calling: some providers require the
                # reasoning back with these calls (see ``llm_gateway.gateway.reasoning_replay``).
                content=text or None,
                reasoning=reasoning or None,
                reasoning_blocks=reasoning_blocks or None,
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

        # Fan out emitted tool calls concurrently; each call takes its own slot (bounded per
        # session and per process) and carries its own budget, so a slow tool cannot hold the
        # whole batch open.
        async def _bounded(tc: dict[str, Any]) -> None:
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
