"""Deterministic, network-free LLM gateway used by tests, CI and E2E runs.

Enabled with ``ROCKET_MODEL_STUB=deterministic``. It implements the same
``LLMGatewayProtocol`` as ``LiteLLMGateway``, so the production dispatch path is
never aware of it (no conditional inside the real gateway, and therefore no way
for stub behaviour to leak into production).

Output is a pure function of the request, so an end-to-end suite can assert on
exact text without ever depending on a provider's latency, availability or
sampling.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import AsyncIterator
from typing import Any

from llm_gateway.pricing import estimate_cost_usd
from specifications.interfaces.llm import (
    BudgetGuardProtocol,
    CallRefusal,
    ChatMessage,
    CredentialResolverProtocol,
    LLMGatewayProtocol,
    ModelRequest,
    StreamChunk,
    UsageEvent,
    UsageSinkProtocol,
    current_model_scope,
)

STUB_ENV_FLAG = "ROCKET_MODEL_STUB"
STUB_MODE = "deterministic"
_TRUTHY = {"1", "true", "yes", "on", STUB_MODE}

# Prefixes the stub reacts to. Kept explicit so a test can request a specific
# response shape instead of guessing at model behaviour.
TOOL_MARKER = "[stub:tool]"
QUESTION_MARKER = "[stub:question]"
# A command (as opposed to the read-only tool above): what permission modes gate.
BASH_MARKER = "[stub:bash]"
# A file write: what the cockpit's list of changes shows.
WRITE_MARKER = "[stub:write]"
STUB_WRITTEN_PATH = "notes/written_by_agent.md"

# marker -> (tool, JSON arguments, what the stub says it is doing)
_MARKER_CALLS: dict[str, tuple[str, str, str]] = {
    TOOL_MARKER: (
        "list_dir",
        '{"path": "/workspace"}',
        "inspecting the workspace before answering",
    ),
    BASH_MARKER: (
        "bash_exec",
        '{"command": "echo stub-ran", "cwd": "/workspace"}',
        "running a command in the workspace",
    ),
    WRITE_MARKER: (
        "file_write",
        '{"path": "' + STUB_WRITTEN_PATH + '", "content": "# Written by the agent\\n"}',
        "writing a note in the workspace",
    ),
}


def stub_enabled(env: dict[str, str] | None = None) -> bool:
    """Whether the deterministic stub should replace the real gateway."""
    source = env if env is not None else os.environ
    return source.get(STUB_ENV_FLAG, "").strip().lower() in _TRUTHY


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _split_for_streaming(text: str, parts: int = 3) -> list[str]:
    """Split text into deterministically sized chunks to mimic token streaming."""
    if not text:
        return []
    size = max(1, len(text) // parts)
    return [text[index : index + size] for index in range(0, len(text), size)]


def _example_for_schema(schema: dict[str, Any], name: str = "") -> Any:
    """Build a deterministic, schema-valid example so constrained decoding is testable.

    Strings carry their property name (``"stub suggestion"``) so a caller that
    validates length or word-count still receives a usable value under the stub.
    """
    if not isinstance(schema, dict):
        return {}
    properties = schema.get("properties")
    if schema.get("type") == "object" or properties:
        return {
            prop_name: _example_for_schema(spec if isinstance(spec, dict) else {}, prop_name)
            for prop_name, spec in (properties or {}).items()
        }
    schema_type = schema.get("type")
    if schema_type == "array":
        return []
    if schema_type == "integer":
        return 0
    if schema_type == "number":
        return 0.0
    if schema_type == "boolean":
        return True
    return f"stub {name}".strip()


# The engine's per-request notes (``agent_core.notes``); not what a person asked.
_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)


class DeterministicStubGateway(LLMGatewayProtocol):
    """Scripted gateway whose streamed output depends only on the request content."""

    def __init__(self, *, chunk_delay_seconds: float = 0.0) -> None:
        self._chunk_delay = chunk_delay_seconds
        self.call_count = 0
        self.usage_ledger: list[dict[str, object]] = []
        # The same policy hooks as the real gateway, so budgets and the persisted ledger are
        # exercised end to end by suites that run on the stub.
        self.usage_sink: UsageSinkProtocol | None = None
        self.budget_guard: BudgetGuardProtocol | None = None
        self.credential_resolver: CredentialResolverProtocol | None = None

    @staticmethod
    def _prompt_text(messages: list[ChatMessage]) -> str:
        """The freshest input the model is expected to answer: the latest user or tool message.

        Tool results count because that is what a real model responds to after a tool
        call. The engine's ``<system-reminder>`` notes are context, not the input, so they are
        skipped; falling back to the very last message keeps the stub total.
        """
        for message in reversed(messages):
            if message.role in ("user", "tool"):
                text = _REMINDER.sub("", message.content or "").strip()
                if text:
                    return text
        return (messages[-1].content or "") if messages else ""

    def _script(self, prompt: str) -> tuple[str, str, dict[str, object] | None]:
        """Return ``(reasoning, answer, tool_call)`` for a prompt."""
        fingerprint = _fingerprint(prompt)
        summary = " ".join(prompt.split())[:120]

        for marker, (name, arguments, intent) in _MARKER_CALLS.items():
            if marker in prompt:
                call: dict[str, object] = {
                    "index": 0,
                    "id": f"call_stub_{fingerprint}",
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
                return f"Stub reasoning [{fingerprint}]: {intent}.", "", call

        reasoning = f"Stub reasoning [{fingerprint}]: single pass, no tools required."
        if QUESTION_MARKER in prompt:
            answer = f"Stub clarifying question [{fingerprint}]: which target did you mean?"
        else:
            answer = f"Stub answer [{fingerprint}] for: {summary}"
        return reasoning, answer, None

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        """Stream a deterministic completion for the request."""
        scope = current_model_scope.get()
        if scope is not None and self.budget_guard is not None:
            try:
                reservation = await self.budget_guard.reserve(
                    scope, estimate_usd=0.0, key_source="platform"
                )
                if reservation is not None:
                    await self.budget_guard.settle(reservation, actual_usd=0.0)
            except Exception as err:
                yield StreamChunk(
                    is_finished=True,
                    finish_reason="error",
                    error=str(err),
                    refusal=CallRefusal.from_error(err),
                )
                return
        self.call_count += 1
        prompt = self._prompt_text(request.messages)
        if request.response_schema is not None:
            # Constrained decoding: emit deterministic, schema-valid JSON so the
            # structured-output path is exercised end to end without a provider.
            reasoning = f"Stub structured reasoning [{_fingerprint(prompt)}]: emitting JSON."
            answer = json.dumps(_example_for_schema(request.response_schema), sort_keys=True)
            tool_call: dict[str, object] | None = None
        else:
            reasoning, answer, tool_call = self._script(prompt)

        if self._chunk_delay:
            await asyncio.sleep(self._chunk_delay)

        for piece in _split_for_streaming(reasoning, parts=2):
            yield StreamChunk(reasoning_delta=piece)

        if tool_call is not None:
            yield StreamChunk(tool_call_delta=tool_call)

        for piece in _split_for_streaming(answer):
            yield StreamChunk(text_delta=piece)

        prompt_tokens = sum(len(message.content or "") for message in request.messages) // 4
        completion_tokens = (len(reasoning) + len(answer)) // 4
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        self.usage_ledger.append({"model": request.model, **usage})
        yield StreamChunk(
            is_finished=True,
            finish_reason="tool_calls" if tool_call is not None else "stop",
            token_usage={key: int(value) for key, value in usage.items()},
        )

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        """The stub never blocks a turn on budget grounds."""
        return True

    async def record_usage(
        self,
        tenant_org_id: str,
        tenant_user_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> None:
        """Record token accounting in memory so tests can assert on it."""
        if self.usage_sink is not None:
            await self.usage_sink.record(
                UsageEvent(
                    org_id=tenant_org_id,
                    user_id=tenant_user_id,
                    session_id=(scope.session_id if (scope := current_model_scope.get()) else None),
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_creation_tokens=cache_creation_tokens,
                    cost_usd=estimate_cost_usd(model, prompt_tokens, completion_tokens),
                )
            )
        self.usage_ledger.append(
            {
                "tenant_org_id": tenant_org_id,
                "tenant_user_id": tenant_user_id,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "cost_usd": 0.0,
                "cache_read_tokens": cache_read_tokens,
                "cache_creation_tokens": cache_creation_tokens,
            }
        )

    @staticmethod
    def _sum_tokens(entries: list[dict[str, object]], field: str) -> int:
        return sum(int(value) for entry in entries if isinstance(value := entry.get(field), int))

    def get_user_usage_summary(self, tenant_user_id: str) -> dict[str, object]:
        """Aggregate the ledger for a user (same shape as the real gateway)."""
        entries = [e for e in self.usage_ledger if e.get("tenant_user_id") == tenant_user_id]
        prompt_tokens = self._sum_tokens(entries, "prompt_tokens")
        completion_tokens = self._sum_tokens(entries, "completion_tokens")
        return {
            "tenant_user_id": tenant_user_id,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_cost_usd": 0.0,
            "cache_read_tokens": self._sum_tokens(entries, "cache_read_tokens"),
            "recent_turns_count": len(entries),
        }
