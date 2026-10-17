"""Replay gateway for incident regression testing.

Loads a recorded incident fixture (such as session_d5d6109b52), yields chunks matching
each recorded model step, and captures all incoming ModelRequests for inspection.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from specifications.interfaces.llm import (
    LLMGatewayProtocol,
    ModelRequest,
    ModelScope,
    StreamChunk,
    current_model_scope,
)


class ReplayGateway(LLMGatewayProtocol):
    """Replays recorded model responses step-by-step from an incident fixture."""

    def __init__(self, fixture_path: Path | str) -> None:
        path = Path(fixture_path)
        if path.is_dir():
            path = path / f"{path.name}.json"
        self.data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        self.session_id: str = self.data.get("session_id", "session_replay")
        self.model_name: str = self.data.get("model", "replay-model")
        self.memory_note: str = self.data.get("memory_note", "")
        self.raw_steps: list[dict[str, Any]] = self.data.get("steps", [])
        self.step_cursor: int = 0
        self.requests: list[ModelRequest] = []
        self.scopes: list[ModelScope | None] = []

    @property
    def last_request(self) -> ModelRequest | None:
        return self.requests[-1] if self.requests else None

    def request_notes(self, index: int = -1) -> str:
        if not self.requests or abs(index) > len(self.requests):
            return ""
        req = self.requests[index]
        if not req.messages:
            return ""
        last = req.messages[-1]
        return (last.content or "") if last.role == "user" else ""

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        self.scopes.append(current_model_scope.get())

        if self.step_cursor >= len(self.raw_steps):
            yield StreamChunk(text_delta="Replay finished.", is_finished=True, finish_reason="stop")
            return

        step = self.raw_steps[self.step_cursor]
        self.step_cursor += 1
        kind = step.get("kind", "say")
        usage = step.get("usage")

        if kind == "call":
            tool_name = step.get("tool", "unnamed_tool")
            tool_args = step.get("arguments", {})
            args_str = json.dumps(tool_args) if isinstance(tool_args, dict) else str(tool_args)
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": f"call_{self.step_cursor}",
                    "function": {"name": tool_name, "arguments": args_str},
                }
            )
            yield StreamChunk(is_finished=True, finish_reason="tool_calls", token_usage=usage)
        elif kind == "think":
            finish_reason = step.get("finish_reason", "length")
            reasoning = step.get("reasoning", "")
            if reasoning:
                yield StreamChunk(reasoning_delta=reasoning)
            yield StreamChunk(is_finished=True, finish_reason=finish_reason, token_usage=usage)
        elif kind == "say":
            text = step.get("text", "")
            finish_reason = step.get("finish_reason", "stop")
            if text:
                yield StreamChunk(text_delta=text)
            yield StreamChunk(is_finished=True, finish_reason=finish_reason, token_usage=usage)
        elif kind == "empty":
            finish_reason = step.get("finish_reason", "length")
            yield StreamChunk(is_finished=True, finish_reason=finish_reason, token_usage=usage)

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(self, *args: Any, **kwargs: Any) -> None:
        return None
