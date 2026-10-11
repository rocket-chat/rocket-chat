"""
Context hygiene and execution scope management for resilient agent orchestration.

Implements 3-tier hybrid compaction:
- Tier 1: Deterministic head/tail output clamping (via agent_core.tools.clamp).
- Tier 2: Tool-payload pruning replacing historical verbose results with receipts.
- Tier 3: Invariant anchor compaction preserving system prompt, initial user prompt,
  checklists, and the latest turns while summarizing filler.

Also provides coroutine/thread-safe ExecutionContext via standard Python contextvars.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import litellm

from agent_core.transcript import repair_tool_pairs
from specifications.interfaces.agent import AgentEvent, TurnCallbacks, TurnPolicy
from specifications.interfaces.llm import AttachmentRef, ChatMessage

Summarizer = Callable[[list[ChatMessage]], Awaitable[str]]
logger = logging.getLogger(__name__)

DEFAULT_MODEL_CONTEXT_LIMITS: dict[str, int] = {
    "claude-3-7-sonnet": 200_000,
    "claude-3.7-sonnet": 200_000,
    "claude-3-5-sonnet": 200_000,
    "gpt-4o": 128_000,
    "gpt-4.5": 128_000,
    "o1": 200_000,
    "o3-mini": 200_000,
    "deepseek-r1": 64_000,
    "deepseek-v3": 64_000,
    "deepseek-v4.1": 131_072,
    "deepseek-v4.1-flash": 131_072,
    "gemini-2.0-flash": 1_000_000,
    "gemini-2.5-pro": 1_000_000,
}
FALLBACK_CONTEXT_LIMIT = 128_000


@dataclass
class ExecutionContext:
    session_id: str
    event_queue: asyncio.Queue[AgentEvent | None]
    parent_model: str
    tenant_org_id: str = "default_org"
    tenant_user_id: str = "dev_user"
    plan_mode: bool = False
    # Set once the workspace has been snapshotted for this turn, before its first change.
    checkpointed: bool = False
    # Enforced tool scope for constrained callers (e.g. the unauthenticated Slack path).
    # None means no restriction; a frozenset is an allowlist the registry enforces server-side.
    allowed_tools: frozenset[str] | None = None
    # Model data governance for every request this turn makes: propagated
    # onto each ModelRequest so the gateway can pass it to the provider.
    zero_retention: bool = False
    region: str = ""
    # Optional toolsets enabled for this turn (e.g. "slack"). Tools tagged with a toolset
    # are only visible/callable when their tag is in this set.
    enabled_toolsets: frozenset[str] = frozenset()
    # Turn-scoped tool collaborators (approvals, questions, sandbox provisioning, ...).
    callbacks: TurnCallbacks = field(default_factory=TurnCallbacks)
    # Organization/agent policy for this turn: generation parameters and git rules.
    policy: TurnPolicy = field(default_factory=TurnPolicy)
    # Copied from the turn policy so the tool registry can read it from the context.
    sandbox_capabilities: frozenset[str] | None = None
    # Images the agent asked to look at this turn (view_image). They ride every model request
    # of the turn but are never stored in the conversation, so they cannot reach a later turn.
    turn_images: list[AttachmentRef] = field(default_factory=list)


current_execution_ctx: contextvars.ContextVar[ExecutionContext | None] = contextvars.ContextVar(
    "current_execution_ctx", default=None
)


class ContextManager:
    """Manages token budgeting, Tier 2 tool payload pruning, and Tier 3 anchor compaction."""

    def __init__(
        self,
        tier2_threshold_ratio: float = 0.70,
        tier3_threshold_ratio: float = 0.85,
        recent_turns_to_preserve: int = 3,
    ) -> None:
        self.tier2_threshold_ratio = tier2_threshold_ratio
        self.tier3_threshold_ratio = tier3_threshold_ratio
        self.recent_turns_to_preserve = recent_turns_to_preserve

    def get_model_context_limit(self, model: str) -> int:
        clean_name = model.split("/")[-1].lower()
        for key, limit in DEFAULT_MODEL_CONTEXT_LIMITS.items():
            if key in clean_name:
                return limit
        try:
            # `max_input_tokens` is the context window; `get_max_tokens` is the *output* cap.
            info = litellm.get_model_info(model)
            window = info.get("max_input_tokens") or info.get("max_tokens")
            if window and isinstance(window, int):
                return window
        except Exception:
            pass
        return FALLBACK_CONTEXT_LIMIT

    def count_tokens(self, model: str, messages: list[ChatMessage]) -> int:
        formatted_messages: list[dict[str, Any]] = []
        for m in messages:
            entry: dict[str, Any] = {"role": m.role, "content": m.content or ""}
            formatted_messages.append(entry)

        try:
            return int(litellm.token_counter(model=model, messages=formatted_messages))
        except Exception:
            # Fallback estimation: ~4 chars per token
            total_chars = sum(len(m.content or "") for m in messages)
            return max(1, total_chars // 4)

    def prune_tool_payloads(
        self,
        messages: list[ChatMessage],
        recent_turns_to_preserve: int | None = None,
    ) -> list[ChatMessage]:
        """
        Tier 2 Compaction: Replace verbose tool results from older turns with compact receipts.
        Preserves assistant thoughts, user prompts, and exact message order / tool_call_id parity.
        """
        preserve_count = (
            recent_turns_to_preserve
            if recent_turns_to_preserve is not None
            else self.recent_turns_to_preserve
        )
        if len(messages) <= (preserve_count * 2):
            return list(messages)

        split_index = max(0, len(messages) - (preserve_count * 2))
        pruned_messages: list[ChatMessage] = []

        for idx, msg in enumerate(messages):
            if idx < split_index and msg.role == "tool" and msg.content:
                lines = msg.content.splitlines()
                line_count = len(lines)
                if line_count > 5 or len(msg.content) > 300:
                    receipt = (
                        f"[Tool result ({line_count} lines, {len(msg.content)} chars) "
                        "pruned to preserve active reasoning context]"
                    )
                    pruned_messages.append(
                        ChatMessage(
                            role=msg.role,
                            content=receipt,
                            tool_call_id=msg.tool_call_id,
                            tool_calls=msg.tool_calls,
                            reasoning=msg.reasoning,
                        )
                    )
                    continue

            pruned_messages.append(msg)

        return pruned_messages

    def _partition(
        self, messages: list[ChatMessage], recent_turns_to_preserve: int | None
    ) -> tuple[list[ChatMessage], list[ChatMessage], list[ChatMessage]] | None:
        """Split into (anchored head, compactable middle, preserved tail), pair-safely."""
        preserve_count = (
            recent_turns_to_preserve
            if recent_turns_to_preserve is not None
            else self.recent_turns_to_preserve
        )
        if len(messages) <= (preserve_count * 2) + 2:
            return None

        anchors_head: list[ChatMessage] = []
        idx = 0
        while idx < len(messages) and messages[idx].role == "system":
            anchors_head.append(messages[idx])
            idx += 1

        # Initial user prompt (and assistant response if immediate)
        if idx < len(messages) and messages[idx].role == "user":
            anchors_head.append(messages[idx])
            idx += 1

        tail_start = max(idx, len(messages) - (preserve_count * 2))
        # Never start the preserved tail on a tool result: its assistant tool call would be
        # summarized away and providers reject an orphan `tool` message.
        while tail_start > idx and messages[tail_start].role == "tool":
            tail_start -= 1
        return anchors_head, messages[idx:tail_start], messages[tail_start:]

    @staticmethod
    def _latest_checklist(middle: list[ChatMessage]) -> str | None:
        for message in reversed(middle):
            for call in message.tool_calls or []:
                function = call.get("function", {})
                if function.get("name") == "update_task_checklist":
                    arguments = function.get("arguments")
                    return str(arguments) if arguments else None
        return None

    def _bridge(self, middle: list[ChatMessage], summary: str | None) -> ChatMessage:
        parts: list[str] = [
            f"[Compacted {len(middle)} intermediate conversational turns "
            "to respect context window boundaries.]"
        ]
        if summary:
            parts.append(f"Summary of the compacted work:\n{summary}")
        plan = self._latest_checklist(middle)
        if plan:
            parts.append(f"Active Task Checklist State: {plan}")
        return ChatMessage(role="system", content="\n\n".join(parts))

    def compact_with_anchors(
        self,
        messages: list[ChatMessage],
        recent_turns_to_preserve: int | None = None,
    ) -> list[ChatMessage]:
        """
        Tier 3 Compaction: Anchor system prompt, initial user prompt, active checklist,
        and last N turns. Middle conversational turns are replaced by a concise bridge.
        """
        parts = self._partition(messages, recent_turns_to_preserve)
        if parts is None:
            return list(messages)
        head, middle, tail = parts
        return repair_tool_pairs([*head, self._bridge(middle, None), *tail])

    async def compact_with_summary(
        self,
        messages: list[ChatMessage],
        summarize: Summarizer,
        recent_turns_to_preserve: int | None = None,
    ) -> list[ChatMessage]:
        """Tier 3 with a model-written summary of the middle; falls back to the bridge."""
        parts = self._partition(messages, recent_turns_to_preserve)
        if parts is None:
            return list(messages)
        head, middle, tail = parts
        summary: str | None = None
        try:
            summary = (await summarize(middle)).strip() or None
        except Exception as err:
            logger.warning("Summarising compaction failed, using the bridge only: %s", err)
        return repair_tool_pairs([*head, self._bridge(middle, summary), *tail])

    def enforce_hygiene(
        self,
        model: str,
        messages: list[ChatMessage],
    ) -> list[ChatMessage]:
        """Runs progressive Tier 2 & Tier 3 hygiene checks against model context limits."""
        limit = self.get_model_context_limit(model)
        tokens = self.count_tokens(model, messages)

        # Tier 2 check (> 70%)
        if tokens >= int(limit * self.tier2_threshold_ratio):
            messages = self.prune_tool_payloads(messages)
            tokens = self.count_tokens(model, messages)

        # Tier 3 check (> 85%)
        if tokens >= int(limit * self.tier3_threshold_ratio):
            messages = self.compact_with_anchors(messages)

        return messages

    async def enforce_hygiene_async(
        self,
        model: str,
        messages: list[ChatMessage],
        summarize: Summarizer | None = None,
    ) -> list[ChatMessage]:
        """Like :meth:`enforce_hygiene`, but Tier 3 summarizes with a model when given one."""
        limit = self.get_model_context_limit(model)
        tokens = self.count_tokens(model, messages)
        if tokens >= int(limit * self.tier2_threshold_ratio):
            messages = self.prune_tool_payloads(messages)
            tokens = self.count_tokens(model, messages)
        if tokens >= int(limit * self.tier3_threshold_ratio):
            if summarize is not None:
                return await self.compact_with_summary(messages, summarize)
            return self.compact_with_anchors(messages)
        return messages


def render_transcript(messages: list[ChatMessage], max_chars: int = 24_000) -> str:
    """Plain-text rendering of messages for a summarizer prompt (bounded)."""
    lines: list[str] = []
    for message in messages:
        if message.tool_calls:
            names = ", ".join(
                str(call.get("function", {}).get("name", "?")) for call in message.tool_calls
            )
            lines.append(f"assistant called tools: {names}")
        if message.content:
            snippet = " ".join(message.content.split())[:600]
            lines.append(f"{message.role}: {snippet}")
    text = "\n".join(lines)
    return text[-max_chars:] if len(text) > max_chars else text
