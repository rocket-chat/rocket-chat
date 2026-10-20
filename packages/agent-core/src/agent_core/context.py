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
import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from fnmatch import fnmatch
from typing import Any

import litellm
from llm_gateway.tokens import request_tokens

from agent_core.notes import REMINDER_CLOSE, REMINDER_OPEN, render_notes
from agent_core.transcript import repair_tool_pairs
from specifications.interfaces.agent import AgentEvent, TurnCallbacks, TurnPolicy
from specifications.interfaces.llm import (
    AttachmentRef,
    ChatMessage,
    ModelCatalogueProtocol,
    ToolDefinition,
)

Summarizer = Callable[[list[ChatMessage]], Awaitable[str]]
logger = logging.getLogger(__name__)

# Families the provider library may not list under the name a gateway (OpenRouter, a BYOK
# proxy) uses. Consulted after the organization's own sizes and the library's model table.
DEFAULT_MODEL_CONTEXT_LIMITS: dict[str, int] = {
    "claude": 200_000,
    "gpt-4o": 128_000,
    "gpt-4.1": 1_000_000,
    "gpt-5": 400_000,
    "o3": 200_000,
    "o4": 200_000,
    "deepseek-r1": 64_000,
    "deepseek-v3": 64_000,
    "deepseek-v4": 131_072,
    "gemini": 1_000_000,
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
    # The checklist as the agent last sent it this turn, so an unchanged re-send is a no-op.
    checklist: list[dict[str, Any]] | None = None
    # Enforced tool scope for constrained callers (e.g. the unauthenticated Slack path).
    # None means no restriction; a frozenset is an allowlist the registry enforces server-side.
    allowed_tools: frozenset[str] | None = None
    # Never usable in this turn, whatever the allowlist (e.g. Slack's blocked tools).
    denied_tools: frozenset[str] = frozenset()
    # Allowed tools whose definitions are only sent once loaded (``load_tools``), and those the
    # session has loaded so far.
    deferred_tools: frozenset[str] = frozenset()
    loaded_tools: set[str] = field(default_factory=set)
    loaded_tools_order: list[str] = field(default_factory=list)
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
    # Where git, test and build tools act when the agent names no folder. Seeded from the
    # turn policy and set by the first ``clone_repository`` of a session that has none.
    active_repository: str = ""
    # Variables the agent set for the session's commands (``env_set``), seeded from the policy.
    agent_env: dict[str, str] = field(default_factory=dict)
    # The prompt size the provider reported for the turn's latest request (0 until one did, and
    # again after a compaction): the floor for the next request's estimate.
    measured_prompt_tokens: int = 0
    # Tracks consecutive model calls with < 20% prompt cache hit rate for alert detection (G4).
    consecutive_low_cache_calls: int = 0
    # Turn index in session execution (used for offload outputs naming and traces)
    turn_index: int = 1


current_execution_ctx: contextvars.ContextVar[ExecutionContext | None] = contextvars.ContextVar(
    "current_execution_ctx", default=None
)


class CompactionResult(list[ChatMessage]):
    """Result of context hygiene pass containing messages and compaction metadata."""

    def __init__(
        self,
        messages: Sequence[ChatMessage] = (),
        *,
        compacted: bool = False,
        tokens_before: int = 0,
        tokens_after: int = 0,
        summary: str | None = None,
        compacted_count: int = 0,
        compacted_prefix: Sequence[ChatMessage] = (),
        tail_count: int = 0,
    ) -> None:
        super().__init__(messages)
        self.compacted = compacted
        self.tokens_before = tokens_before
        self.tokens_after = tokens_after
        self.summary = summary
        self.compacted_count = compacted_count
        self.compacted_prefix = list(compacted_prefix)
        self.tail_count = tail_count


@dataclass
class _Compaction:
    """The pieces and the result of one Tier-3 compaction."""

    head: list[ChatMessage]
    middle: list[ChatMessage]
    tail: list[ChatMessage]
    bridge: ChatMessage
    summary: str | None
    composed: list[ChatMessage]


class ContextManager:
    """Manages token budgeting, Tier 2 tool payload pruning, and Tier 3 anchor compaction."""

    def __init__(
        self,
        tier2_threshold_ratio: float = 0.70,
        tier3_threshold_ratio: float = 0.85,
        target_prune_ratio: float = 0.45,
        target_summary_ratio: float = 0.35,
        recent_turns_to_preserve: int = 3,
        min_steps_between_compactions: int = 5,
        emergency_threshold_ratio: float = 0.90,
        catalogue: ModelCatalogueProtocol | None = None,
    ) -> None:
        self.tier2_threshold_ratio = tier2_threshold_ratio
        self.tier3_threshold_ratio = tier3_threshold_ratio
        self.target_prune_ratio = target_prune_ratio
        self.target_summary_ratio = target_summary_ratio
        self.recent_turns_to_preserve = recent_turns_to_preserve
        self.min_steps_between_compactions = min_steps_between_compactions
        self.emergency_threshold_ratio = emergency_threshold_ratio
        self.catalogue = catalogue

    def get_model_context_limit(self, model: str, overrides: Sequence[tuple[str, int]] = ()) -> int:
        """The model's input window: the organization's own size, the catalogue, the library's, a family's."""
        name = model.lower()
        for pattern, size in overrides:
            if name == pattern.lower() or fnmatch(name, pattern.lower()):
                return size
        if self.catalogue is not None:
            spec = self.catalogue.get_model_spec(model)
            if spec and spec.context_window > 0:
                return spec.context_window
        try:
            # `max_input_tokens` is the context window; `get_max_tokens` is the *output* cap.
            info = litellm.get_model_info(model)
            window = info.get("max_input_tokens") or info.get("max_tokens")
            if window and isinstance(window, int):
                return window
        except Exception:
            pass
        clean_name = name.split("/")[-1]
        for key, limit in DEFAULT_MODEL_CONTEXT_LIMITS.items():
            if key in clean_name:
                return limit
        return FALLBACK_CONTEXT_LIMIT

    def count_tokens(
        self, model: str, messages: list[ChatMessage], tools: Sequence[ToolDefinition] = ()
    ) -> int:
        """Everything the request sends (see ``llm_gateway.tokens.request_tokens``)."""
        return request_tokens(model, messages, tools)

    def prune_tool_payloads(
        self,
        messages: list[ChatMessage],
        recent_turns_to_preserve: int | None = None,
    ) -> list[ChatMessage]:
        """
        Tier 2 Compaction: Replace verbose tool results and the large arguments of older calls
        (a file written, a patch, an artifact) with compact receipts.
        Preserves assistant thoughts, user prompts, and exact message order / tool_call_id parity.
        """
        preserve_count = (
            recent_turns_to_preserve
            if recent_turns_to_preserve is not None
            else self.recent_turns_to_preserve
        )
        split_index = self._recent_start(messages, preserve_count)
        if split_index is None:
            return list(messages)
        names = _tool_names(messages)
        pruned_messages: list[ChatMessage] = []

        for idx, msg in enumerate(messages):
            if idx >= split_index:
                pruned_messages.append(msg)
            elif msg.role == "tool" and msg.content:
                lines = msg.content.splitlines()
                if len(lines) > 5 or len(msg.content) > 300:
                    receipt = _result_receipt(msg.content, names.get(msg.tool_call_id or "", ""))
                    pruned_messages.append(replace(msg, content=receipt))
                else:
                    pruned_messages.append(msg)
            elif msg.role == "assistant" and msg.tool_calls:
                pruned_messages.append(replace(msg, tool_calls=_prune_arguments(msg.tool_calls)))
            else:
                pruned_messages.append(msg)

        return pruned_messages

    def _recent_start(self, messages: list[ChatMessage], steps: int) -> int | None:
        """Where the last ``steps`` model steps begin (an assistant message), or ``None`` when the
        conversation has no more steps than that. A step is never split from its tool results."""
        boundaries = [i for i, message in enumerate(messages) if message.role == "assistant"]
        if len(boundaries) <= steps:
            return None
        return boundaries[-steps]

    def _partition(
        self, messages: list[ChatMessage], recent_turns_to_preserve: int | None
    ) -> tuple[list[ChatMessage], list[ChatMessage], list[ChatMessage]] | None:
        """Split into (anchors, compactable middle, preserved tail).

        The anchors are the system prompt and the request the turn is working on (the latest
        thing the person asked), never the session's first message: in a long session that is
        an old question, and compacting the current one away would lose the goal.
        """
        steps = (
            recent_turns_to_preserve
            if recent_turns_to_preserve is not None
            else self.recent_turns_to_preserve
        )
        tail_start = self._recent_start(messages, steps)
        if tail_start is None:
            return None
        lead = 0
        while lead < len(messages) and messages[lead].role == "system":
            lead += 1
        current = _current_request(messages)
        anchored = current is not None and lead <= current < tail_start
        anchor_msgs: list[ChatMessage] = []
        if anchored and current is not None:
            anchor = messages[current]
            content = anchor.content or ""
            if REMINDER_OPEN in content and "[Compacted " in content:
                cleaned = re.sub(
                    rf"{re.escape(REMINDER_OPEN)}\s*\[Compacted[\s\S]*?{re.escape(REMINDER_CLOSE)}",
                    "",
                    content,
                ).strip()
                anchor = replace(anchor, content=cleaned)
            anchor_msgs.append(anchor)
        head = [*messages[:lead], *anchor_msgs]
        middle = [
            message
            for index, message in enumerate(messages[lead:tail_start], start=lead)
            if not (anchored and index == current)
        ]
        if not middle:
            return None
        return head, middle, messages[tail_start:]

    @staticmethod
    def _latest_checklist(middle: list[ChatMessage]) -> str | None:
        for message in reversed(middle):
            for call in message.tool_calls or []:
                function = call.get("function", {})
                if function.get("name") == "update_task_checklist":
                    arguments = function.get("arguments")
                    return str(arguments) if arguments else None
        return None

    def _bridge(
        self,
        middle: list[ChatMessage],
        summary: str | None,
        working_plan: str | None = None,
        progress_md: str | None = None,
    ) -> ChatMessage:
        # A user-role reminder, not a system message: providers that hoist system messages into
        # the system prompt would lose where the summary sits in the conversation.
        parts: list[str] = [
            f"[Compacted {len(middle)} intermediate conversational turns "
            "to respect context window boundaries.]"
        ]
        if summary:
            parts.append(f"Summary of the compacted work:\n{summary}")
        plan = self._latest_checklist(middle)
        if plan:
            parts.append(f"Active Task Checklist State: {plan}")
        if working_plan:
            parts.append(f"Working Plan (.rocket/PLAN.md):\n{working_plan}")
        if progress_md:
            parts.append(f"Working Progress (.rocket/PROGRESS.md):\n{progress_md}")
        return ChatMessage(role="user", content=render_notes(parts))

    async def _compact(
        self,
        messages: list[ChatMessage],
        summarize: Summarizer | None,
        recent_turns_to_preserve: int | None,
        working_plan: str | None,
        progress_md: str | None,
    ) -> _Compaction | None:
        """The one compaction both entry points use: anchors kept, the middle summarised."""
        parts = self._partition(messages, recent_turns_to_preserve)
        if parts is None:
            return None
        head, middle, tail = parts
        summary: str | None = None
        if summarize is not None:
            goal = [
                replace(
                    message,
                    content=f"[The request being worked on, kept verbatim] {message.content}",
                )
                for message in head
                if message.role == "user"
            ]
            try:
                summary = (await summarize([*goal, *middle])).strip() or None
            except Exception as err:
                logger.warning("Summarising compaction failed, using the bridge only: %s", err)
        bridge = self._bridge(middle, summary, working_plan, progress_md)
        return _Compaction(head, middle, tail, bridge, summary, _compose(head, bridge, tail))

    async def compact_with_summary(
        self,
        messages: list[ChatMessage],
        summarize: Summarizer | None = None,
        recent_turns_to_preserve: int | None = None,
        working_plan: str | None = None,
        progress_md: str | None = None,
    ) -> list[ChatMessage]:
        """Tier 3: anchors kept, the middle replaced by a bridge (with a model-written summary
        when ``summarize`` is given, and when it fails the bridge alone)."""
        compaction = await self._compact(
            messages, summarize, recent_turns_to_preserve, working_plan, progress_md
        )
        return list(messages) if compaction is None else compaction.composed

    def should_compact_tier3(
        self,
        tokens: int,
        limit: int,
        step: int | None = None,
        last_compaction_step: int | None = None,
        force_compaction: bool = False,
    ) -> bool:
        """Determines whether Tier 3 compaction should trigger, enforcing hysteresis."""
        if force_compaction:
            return True
        if tokens < int(limit * self.tier3_threshold_ratio):
            return False
        # If tokens are above emergency threshold (> 90%), compact regardless of step frequency
        if tokens >= int(limit * self.emergency_threshold_ratio):
            return True
        # Hysteresis: don't compact more than once per N steps
        if step is not None and last_compaction_step is not None:
            if step - last_compaction_step < self.min_steps_between_compactions:
                return False
        return True

    async def enforce_hygiene_async(
        self,
        model: str,
        messages: list[ChatMessage],
        summarize: Summarizer | None = None,
        *,
        tools: Sequence[ToolDefinition] = (),
        measured_tokens: int = 0,
        overrides: Sequence[tuple[str, int]] = (),
        step: int | None = None,
        last_compaction_step: int | None = None,
        force_compaction: bool = False,
        working_plan: str | None = None,
        progress_md: str | None = None,
        target_tokens: int = 0,
    ) -> CompactionResult:
        """Runs progressive Tier 2 & Tier 3 hygiene checks against model context limits.

        ``target_tokens`` caps the window the thresholds apply to: a model with a 1M window
        otherwise works for hundreds of steps at half a million tokens per request, each step
        slower and costlier, and loses track of its own progress well before the window ends.
        """
        limit = self.get_model_context_limit(model, overrides)
        if target_tokens > 0:
            limit = min(limit, target_tokens)
        counted = self.count_tokens(model, messages, tools)
        tokens = max(counted, measured_tokens)
        tokens_before = tokens

        # Tier 2 check (> 70%)
        if tokens >= int(limit * self.tier2_threshold_ratio):
            messages = self.prune_tool_payloads(messages)
            pruned = self.count_tokens(model, messages, tools)
            tokens = max(pruned, measured_tokens - (counted - pruned))

        # Tier 3 check (> 85% or forced)
        if self.should_compact_tier3(tokens, limit, step, last_compaction_step, force_compaction):
            compaction = await self._compact(
                messages, summarize, self.recent_turns_to_preserve, working_plan, progress_md
            )
            if compaction is not None:
                lead = 0
                while lead < len(compaction.head) and compaction.head[lead].role == "system":
                    lead += 1
                return CompactionResult(
                    compaction.composed,
                    compacted=True,
                    tokens_before=tokens_before,
                    tokens_after=self.count_tokens(model, compaction.composed, tools),
                    summary=compaction.summary,
                    compacted_count=len(compaction.middle),
                    compacted_prefix=_compose(compaction.head[lead:], compaction.bridge, []),
                    tail_count=len(compaction.tail),
                )

        return CompactionResult(
            messages,
            compacted=False,
            tokens_before=tokens_before,
            tokens_after=tokens,
        )


# Tool-call arguments longer than this (a whole file, a patch, an artifact) are replaced in
# older steps; the file or artifact itself is where the content lives.
ARGUMENT_PRUNE_CHARS = 1_500
_OFFLOAD_PATH = re.compile(r"Full output saved to (/workspace/\.rocket/outputs/\S+?\.txt)")


def _tool_names(messages: Sequence[ChatMessage]) -> dict[str, str]:
    """Tool call id -> tool name, for the receipts of the calls' results."""
    names: dict[str, str] = {}
    for message in messages:
        for call in message.tool_calls or []:
            name = (call.get("function") or {}).get("name")
            if call.get("id") and name:
                names[str(call["id"])] = str(name)
    return names


def _result_receipt(content: str, tool_name: str) -> str:
    """What stands in for an old tool result: its size, and where the full output can be read."""
    label = f"Tool result of {tool_name}" if tool_name else "Tool result"
    saved = _OFFLOAD_PATH.search(content)
    where = f"; full output: {saved.group(1)}" if saved else ""
    return (
        f"[{label} ({len(content.splitlines())} lines, {len(content)} chars) pruned to "
        f"preserve active reasoning context{where}]"
    )


def _prune_arguments(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The calls with every long string argument replaced by its size; the JSON stays valid."""
    pruned: list[dict[str, Any]] = []
    for call in calls:
        function = call.get("function") or {}
        raw = function.get("arguments")
        if not isinstance(raw, str) or len(raw) <= ARGUMENT_PRUNE_CHARS:
            pruned.append(call)
            continue
        try:
            arguments = json.loads(raw)
        except ValueError:
            pruned.append(call)
            continue
        if not isinstance(arguments, dict):
            pruned.append(call)
            continue
        shortened = {
            key: f"[{len(value)} chars omitted from an older step]"
            if isinstance(value, str) and len(value) > ARGUMENT_PRUNE_CHARS // 3
            else value
            for key, value in arguments.items()
        }
        pruned.append({**call, "function": {**function, "arguments": json.dumps(shortened)}})
    return pruned


def _current_request(messages: list[ChatMessage]) -> int | None:
    """Index of the latest message the person wrote (engine reminders are not theirs)."""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.role == "user" and not (message.content or "").startswith(REMINDER_OPEN):
            return index
    return None


def _compose(
    head: list[ChatMessage], bridge: ChatMessage, tail: list[ChatMessage]
) -> list[ChatMessage]:
    """Head, bridge and tail as a valid transcript with no two user messages in a row."""
    merged: list[ChatMessage] = []
    for message in repair_tool_pairs([*head, bridge, *tail]):
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous.role == message.role == "user"
            and not previous.attachments
            and not message.attachments
        ):
            merged[-1] = replace(
                previous, content=f"{previous.content or ''}\n\n{message.content or ''}".strip()
            )
            continue
        merged.append(message)
    return merged


def _render_call(call: dict[str, Any]) -> str:
    function = call.get("function", {})
    name = str(function.get("name", "?"))
    arguments = " ".join(str(function.get("arguments", "")).split())
    limit = 1500 if name == "update_task_checklist" else 300
    return f"{name}({arguments[:limit]}{'...' if len(arguments) > limit else ''})"


def render_transcript(messages: list[ChatMessage], max_chars: int = 120_000) -> str:
    """Plain-text rendering of messages for a summarizer prompt (bounded).

    Each call keeps its arguments (which file, which command, which pull request), since the
    summary is all that survives of them. Over the bound the middle of the rendering is cut,
    not its start: the first steps hold the decisions everything after builds on.
    """
    lines: list[str] = []
    for message in messages:
        if message.reasoning and message.role == "assistant":
            lines.append(f"assistant thought: {' '.join(message.reasoning.split())[:400]}")
        if message.content:
            snippet = " ".join(message.content.split())[:600]
            lines.append(f"{message.role}: {snippet}")
        for call in message.tool_calls or []:
            lines.append(f"assistant called {_render_call(call)}")
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return f"{text[:half]}\n[... {len(text) - max_chars} characters omitted ...]\n{text[-half:]}"
