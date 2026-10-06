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
from dataclasses import dataclass
from typing import Any

import litellm

from specifications.interfaces.agent import AgentEvent
from specifications.interfaces.llm import ChatMessage

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
    "gemini-2.0-flash": 1_000_000,
    "gemini-2.5-pro": 1_000_000,
}
FALLBACK_CONTEXT_LIMIT = 128_000


@dataclass
class ExecutionContext:
    session_id: str
    event_queue: asyncio.Queue[AgentEvent | None]
    parent_model: str


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
            max_tokens = litellm.get_max_tokens(model)
            if max_tokens and isinstance(max_tokens, int):
                return max_tokens
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

    def compact_with_anchors(
        self,
        messages: list[ChatMessage],
        recent_turns_to_preserve: int | None = None,
    ) -> list[ChatMessage]:
        """
        Tier 3 Compaction: Anchor system prompt, initial user prompt, active checklist,
        and last N turns. Middle conversational turns are summarized into a concise bridge.
        """
        preserve_count = (
            recent_turns_to_preserve
            if recent_turns_to_preserve is not None
            else self.recent_turns_to_preserve
        )
        if len(messages) <= (preserve_count * 2) + 2:
            return list(messages)

        anchors_head: list[ChatMessage] = []
        middle_candidates: list[ChatMessage] = []
        recent_tail: list[ChatMessage] = []

        # 1. System prompt
        idx = 0
        if idx < len(messages) and messages[idx].role == "system":
            anchors_head.append(messages[idx])
            idx += 1

        # 2. Initial user prompt (and assistant response if immediate)
        if idx < len(messages) and messages[idx].role == "user":
            anchors_head.append(messages[idx])
            idx += 1

        tail_start = max(idx, len(messages) - (preserve_count * 2))
        middle_candidates = messages[idx:tail_start]
        recent_tail = messages[tail_start:]

        # Extract any active checklist from middle if present
        latest_plan: str | None = None
        for m in reversed(middle_candidates):
            if m.tool_calls:
                for tc in m.tool_calls:
                    fn = tc.get("function", {})
                    if fn.get("name") == "update_task_checklist":
                        latest_plan = fn.get("arguments")
                        break
            if latest_plan:
                break

        summary_parts: list[str] = [
            f"[Compacted {len(middle_candidates)} intermediate conversational turns "
            "to respect context window boundaries.]"
        ]
        if latest_plan:
            summary_parts.append(f"Active Task Checklist State: {latest_plan}")

        summary_msg = ChatMessage(
            role="system",
            content="\n\n".join(summary_parts),
        )

        return [*anchors_head, summary_msg, *recent_tail]

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
