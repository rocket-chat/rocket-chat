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

from agent_core.compaction_summary import (
    CHECKPOINT_MARKER,
    RETRY_NOTE,
    SUMMARY_STATUS_DIGEST,
    SUMMARY_STATUS_MODEL,
    SUMMARY_STATUS_MODEL_RETRY,
    summary_prompt,
    tool_history_digest,
    valid_summary,
)
from agent_core.notes import REMINDER_CLOSE, REMINDER_OPEN, render_notes
from agent_core.skill_blocks import is_skill_result, skill_blocks
from agent_core.transcript import repair_tool_pairs
from specifications.interfaces.agent import AgentEvent, ServerGuidance, TurnCallbacks, TurnPolicy
from specifications.interfaces.llm import (
    AttachmentRef,
    ChatMessage,
    ModelCatalogueProtocol,
    ToolDefinition,
)

# Sends a summary request (the conversation, then the summary prompt) and returns the text.
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
    "deepseek-v4": 1_048_576,
    "gemini": 1_000_000,
    "mimo": 1_000_000,
    "kimi": 256_000,
    "qwen3": 256_000,
    "glm": 200_000,
    "grok": 256_000,
    "llama-4": 1_000_000,
}
FALLBACK_CONTEXT_LIMIT = 128_000
# Where a model's window came from, from the most to the least trustworthy.
LIMIT_OVERRIDE = "override"
LIMIT_PROVIDER = "provider"
LIMIT_LITELLM = "litellm"
LIMIT_FAMILY = "family"
LIMIT_DEFAULT = "default"
# The person's earlier messages kept verbatim across a compaction (after Codex): about 20,000
# tokens, newest first. A constraint they gave at the start must outlive the summary of it.
EARLIER_REQUESTS_BUDGET_CHARS = 80_000


@dataclass(frozen=True)
class ContextLimit:
    """A model's input window and where that figure came from."""

    tokens: int
    source: str


def family_context_limit(model: str) -> int | None:
    """The window of the model family a name belongs to; the longest matching family wins."""
    name = model.lower().split("/")[-1]
    matches = [key for key in DEFAULT_MODEL_CONTEXT_LIMITS if key in name]
    return DEFAULT_MODEL_CONTEXT_LIMITS[max(matches, key=len)] if matches else None


def threshold(limit: int, ratio: float, reserve: int = 0) -> int:
    """Tokens at which a tier triggers: a share of the window, never closer to it than ``reserve``.

    A share alone leaves a 128K window with 13K tokens after a 90% trigger, less than the reply
    a reasoning model may write; the reserve is the room the reply needs. Never below half the
    window, so a large reply budget cannot make a small window compact on every step.
    """
    by_share = int(limit * ratio)
    if reserve <= 0:
        return by_share
    return max(min(by_share, limit - reserve), limit // 2)


_unknown_windows_reported: set[str] = set()


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
    # Shell commands this turn that a dedicated tool covers (see ``tools/shell_intent.py``).
    shell_file_operations: int = 0
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
    # MCP servers with usage guidance this turn, and the guidance the agent has in effect so far
    # (server -> digest). ``guidance_pending`` holds what was put in effect during the turn and is
    # not in the request yet; the orchestrator moves it into the turn's context note.
    guidance_by_server: dict[str, ServerGuidance] = field(default_factory=dict)
    guidance_in_effect: dict[str, str] = field(default_factory=dict)
    guidance_pending: list[ServerGuidance] = field(default_factory=list)
    # A subagent has a conversation of its own and no context note: it reads guidance in the
    # result of ``load_guidance`` instead.
    guidance_in_result: bool = False
    # Skill instructions the agent has in effect (skill -> digest, and the texts), and standing
    # text added this turn that is not in the request yet (skills, a cloned repository's
    # AGENTS.md): the orchestrator moves it into the turn's context note.
    skills_in_effect: dict[str, str] = field(default_factory=dict)
    skill_texts: dict[str, str] = field(default_factory=dict)
    standing_pending: list[str] = field(default_factory=list)
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
    # Set by ``wait_for`` when it registered a wait that will wake the session: only then does
    # the step end the turn (a wait that settled at once is acted on in the same turn).
    wait_registered: bool = False
    # The post-edit diagnostics said once this turn that the project is not set up; later edits
    # with the same environment failure stay quiet.
    setup_noted: bool = False
    # Folders whose setups were checked this turn (``prepare_folder``): checked once, not before
    # every command.
    prepared_folders: set[str] = field(default_factory=set)


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
        summary_status: str = "",
        compacted_count: int = 0,
        compacted_prefix: Sequence[ChatMessage] = (),
        tail_count: int = 0,
    ) -> None:
        super().__init__(messages)
        self.compacted = compacted
        self.tokens_before = tokens_before
        self.tokens_after = tokens_after
        self.summary = summary
        self.summary_status = summary_status
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
    summary_status: str
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
        """The model's input window (see ``resolve_context_limit`` for where it comes from)."""
        return self.resolve_context_limit(model, overrides).tokens

    def resolve_context_limit(
        self, model: str, overrides: Sequence[tuple[str, int]] = ()
    ) -> ContextLimit:
        """The input window: the organization's own size, the catalogue, the library's, a family's.

        A catalogue that only has the default for a model (it never heard of it) does not count:
        the later sources are better informed than a guess.
        """
        name = model.lower()
        for pattern, size in overrides:
            if name == pattern.lower() or fnmatch(name, pattern.lower()):
                return ContextLimit(size, LIMIT_OVERRIDE)
        if self.catalogue is not None:
            spec = self.catalogue.get_model_spec(model)
            source = getattr(spec, "context_source", LIMIT_PROVIDER)
            if spec and spec.context_window > 0 and source != LIMIT_DEFAULT:
                return ContextLimit(spec.context_window, source)
        try:
            # `max_input_tokens` is the context window; `get_max_tokens` is the *output* cap.
            info = litellm.get_model_info(model)
            window = info.get("max_input_tokens") or info.get("max_tokens")
            if window and isinstance(window, int):
                return ContextLimit(window, LIMIT_LITELLM)
        except Exception:
            pass
        if (family := family_context_limit(model)) is not None:
            return ContextLimit(family, LIMIT_FAMILY)
        if name not in _unknown_windows_reported:
            _unknown_windows_reported.add(name)
            logger.warning(
                "No context window known for %s; assuming %d tokens. Set one under "
                "context_windows if it is larger.",
                model,
                FALLBACK_CONTEXT_LIMIT,
            )
        return ContextLimit(FALLBACK_CONTEXT_LIMIT, LIMIT_DEFAULT)

    def count_tokens(
        self, model: str, messages: list[ChatMessage], tools: Sequence[ToolDefinition] = ()
    ) -> int:
        """Everything the request sends (see ``llm_gateway.tokens.request_tokens``)."""
        return request_tokens(model, messages, tools)

    def _prune_single_message(
        self, msg: ChatMessage, names: dict[str, str]
    ) -> tuple[ChatMessage, int]:
        """Prune one message if eligible, returning (new_msg, chars_freed)."""
        if msg.role == "tool" and is_skill_result(
            names.get(msg.tool_call_id or "", ""), msg.content
        ):
            return msg, 0
        if msg.role == "tool" and msg.content:
            lines = msg.content.splitlines()
            if len(lines) > 5 or len(msg.content) > 300:
                receipt = _result_receipt(msg.content, names.get(msg.tool_call_id or "", ""))
                return replace(msg, content=receipt), max(0, len(msg.content) - len(receipt))
            return msg, 0
        if msg.role == "assistant" and msg.tool_calls:
            pruned_calls = _prune_arguments(msg.tool_calls)
            if pruned_calls != msg.tool_calls:
                before_len = sum(len(str(c)) for c in msg.tool_calls)
                after_len = sum(len(str(c)) for c in pruned_calls)
                return replace(msg, tool_calls=pruned_calls), max(0, before_len - after_len)
        return msg, 0

    def prune_tool_payloads(
        self,
        messages: list[ChatMessage],
        recent_turns_to_preserve: int | None = None,
        target_chars_to_free: int | None = None,
    ) -> list[ChatMessage]:
        """
        Tier 2 Compaction: Replace verbose tool results and the large arguments of older calls
        (a file written, a patch, an artifact) with compact receipts.
        When target_chars_to_free is set, prunes oldest step groups forward until the target
        savings are met so large-context models retain recent unpruned steps down to target_prune_ratio.
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
        freed_chars = 0
        stop_pruning = False

        for idx, msg in enumerate(messages):
            if idx >= split_index:
                pruned_messages.append(msg)
                continue
            if (
                target_chars_to_free is not None
                and freed_chars >= target_chars_to_free
                and msg.role == "assistant"
            ):
                stop_pruning = True
            if stop_pruning:
                pruned_messages.append(msg)
                continue
            new_msg, freed = self._prune_single_message(msg, names)
            freed_chars += freed
            pruned_messages.append(new_msg)

        return pruned_messages

    def _recent_start(self, messages: list[ChatMessage], steps: int) -> int | None:
        """Where the last ``steps`` model steps begin (an assistant message), or ``None`` when the
        conversation has no more steps than that. A step is never split from its tool results."""
        boundaries = [i for i, message in enumerate(messages) if message.role == "assistant"]
        if len(boundaries) <= steps:
            return None
        return boundaries[-steps]

    def _recent_start_by_budget(
        self,
        messages: list[ChatMessage],
        max_tail_tokens: int = 24_000,
        min_turns: int = 2,
        max_turns: int = 64,
    ) -> int | None:
        """Where the recent tail begins, accumulating whole step groups up to max_tail_tokens."""
        boundaries = [i for i, message in enumerate(messages) if message.role == "assistant"]
        if len(boundaries) <= min_turns:
            return None

        accumulated_chars = 0
        target_chars = max_tail_tokens * 4
        chosen_steps = min_turns

        for step_idx in range(1, min(len(boundaries), max_turns + 1)):
            start_i = boundaries[-step_idx]
            end_i = boundaries[-step_idx + 1] if step_idx > 1 else len(messages)
            step_chars = sum(
                len(m.content or "") + sum(len(str(c)) for c in (m.tool_calls or []))
                for m in messages[start_i:end_i]
            )
            if step_idx > min_turns and (accumulated_chars + step_chars) > target_chars:
                break
            accumulated_chars += step_chars
            chosen_steps = step_idx

        return boundaries[-chosen_steps]

    def _partition(
        self,
        messages: list[ChatMessage],
        recent_turns_to_preserve: int | None = None,
        max_tail_tokens: int | None = None,
    ) -> tuple[list[ChatMessage], list[ChatMessage], list[ChatMessage]] | None:
        """Split into (anchors, compactable middle, preserved tail).

        The anchors are the system prompt and the request the turn is working on (the latest
        thing the person asked), never the session's first message: in a long session that is
        an old question, and compacting the current one away would lose the goal.
        """
        if max_tail_tokens is not None and recent_turns_to_preserve is None:
            tail_start = self._recent_start_by_budget(messages, max_tail_tokens=max_tail_tokens)
        else:
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
        # An earlier bridge merged into the anchored request: it leaves the anchor, and goes to
        # the middle, so the skills and messages it carried are carried again.
        earlier_bridge: list[ChatMessage] = []
        if anchored and current is not None:
            anchor = messages[current]
            content = anchor.content or ""
            if REMINDER_OPEN in content and "[Compacted " in content:
                pattern = _compacted_blocks()
                earlier_bridge = [
                    ChatMessage(role="user", content=block) for block in pattern.findall(content)
                ]
                anchor = replace(anchor, content=pattern.sub("", content).strip())
            anchor_msgs.append(anchor)
        head = [*messages[:lead], *anchor_msgs]
        middle = earlier_bridge + [
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
        earlier = _earlier_requests(middle)
        if earlier:
            parts.append(
                f"{_EARLIER_HEADING}\n"
                + "\n".join(f"> {' '.join(text.splitlines())}" for text in earlier)
            )
        skills = skill_blocks([m.content or "" for m in middle])
        if skills:
            parts.append(
                "Skill instructions in effect (loaded earlier in this session):\n"
                + "\n\n".join(reversed(list(skills.values())))
            )
        if summary:
            parts.append(f"{CHECKPOINT_MARKER}\n{summary}")
        plan = self._latest_checklist(middle)
        if plan:
            parts.append(f"Active Task Checklist State: {plan}")
        if working_plan:
            parts.append(f"Working Plan (.rocket/PLAN.md):\n{working_plan}")
        if progress_md:
            parts.append(f"Working Progress (.rocket/PROGRESS.md):\n{progress_md}")
        steering = [
            m.content.strip()
            for m in middle
            if m.role == "user"
            and getattr(m, "name", None) == "user_steering"
            and m.content
            and m.content.strip()
        ]
        if steering:
            parts.append(
                "Active mid-turn steering from user:\n"
                + "\n".join(f"- {text}" for text in steering)
            )
        return ChatMessage(role="user", content=render_notes(parts))

    async def _compact(
        self,
        messages: list[ChatMessage],
        summarize: Summarizer | None,
        recent_turns_to_preserve: int | None,
        working_plan: str | None,
        progress_md: str | None,
        max_tail_tokens: int | None = None,
    ) -> _Compaction | None:
        """The one compaction both entry points use: anchors kept, the middle summarised."""
        parts = self._partition(
            messages,
            recent_turns_to_preserve=recent_turns_to_preserve,
            max_tail_tokens=max_tail_tokens,
        )
        if parts is None:
            return None
        head, middle, tail = parts
        summary, status = await _summarize(messages, middle, summarize)
        bridge = self._bridge(middle, summary, working_plan, progress_md)
        return _Compaction(
            head, middle, tail, bridge, summary, status, _compose(head, bridge, tail)
        )

    async def compact_with_summary(
        self,
        messages: list[ChatMessage],
        summarize: Summarizer | None = None,
        recent_turns_to_preserve: int | None = None,
        working_plan: str | None = None,
        progress_md: str | None = None,
        max_tail_tokens: int | None = None,
    ) -> list[ChatMessage]:
        """Tier 3: anchors kept, the middle replaced by a bridge (with a model-written summary
        when ``summarize`` is given, and when it fails the bridge alone)."""
        compaction = await self._compact(
            messages,
            summarize,
            recent_turns_to_preserve,
            working_plan,
            progress_md,
            max_tail_tokens=max_tail_tokens,
        )
        return list(messages) if compaction is None else compaction.composed

    def should_compact_tier3(
        self,
        tokens: int,
        limit: int,
        step: int | None = None,
        last_compaction_step: int | None = None,
        force_compaction: bool = False,
        reserve_tokens: int = 0,
    ) -> bool:
        """Determines whether Tier 3 compaction should trigger, enforcing hysteresis."""
        if force_compaction:
            return True
        if tokens < threshold(limit, self.tier3_threshold_ratio, reserve_tokens):
            return False
        # If tokens are above emergency threshold (> 90%), compact regardless of step frequency
        if tokens >= threshold(limit, self.emergency_threshold_ratio, reserve_tokens):
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
        reserve_tokens: int = 0,
    ) -> CompactionResult:
        """Runs progressive Tier 2 & Tier 3 hygiene checks against model context limits.

        ``target_tokens`` caps the window the thresholds apply to: a model with a 1M window
        otherwise works for hundreds of steps at half a million tokens per request, each step
        slower and costlier, and loses track of its own progress well before the window ends.
        ``reserve_tokens`` is the room the model's reply needs: no tier triggers closer to the
        window than that (a quarter of the window at most).
        """
        limit = self.get_model_context_limit(model, overrides)
        if target_tokens > 0:
            limit = min(limit, target_tokens)
        # Cap reserve_tokens so wildcard output_tokens settings (e.g. 65k) don't eat 50k of working context
        reserve_tokens = min(max(0, reserve_tokens), 16_384, limit // 4)
        counted = self.count_tokens(model, messages, tools)
        tokens = max(counted, measured_tokens)
        tokens_before = tokens

        # Tier 2 check (> 70%): batch-prune oldest payloads down to target_prune_ratio (45%)
        if tokens >= threshold(limit, self.tier2_threshold_ratio, reserve_tokens):
            target_after = int(limit * self.target_prune_ratio)
            target_chars = max(0, (tokens - target_after) * 4)
            messages = self.prune_tool_payloads(messages, target_chars_to_free=target_chars)
            pruned = self.count_tokens(model, messages, tools)
            tokens = max(pruned, measured_tokens - (counted - pruned))

        # Tier 3 check (> 85% or forced)
        if self.should_compact_tier3(
            tokens, limit, step, last_compaction_step, force_compaction, reserve_tokens
        ):
            max_tail = (
                None
                if force_compaction
                else min(80_000, max(4_000, int(limit * self.target_summary_ratio)))
            )
            compaction = await self._compact(
                messages,
                summarize,
                self.recent_turns_to_preserve if force_compaction else None,
                working_plan,
                progress_md,
                max_tail_tokens=max_tail,
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
                    summary_status=compaction.summary_status,
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


async def _ask_for_summary(summarize: Summarizer, request: list[ChatMessage]) -> str | None:
    try:
        return (await summarize(request)).strip()
    except Exception as err:
        logger.warning("Summarising a compaction failed: %s", err)
        return None


async def _summarize(
    messages: list[ChatMessage], middle: list[ChatMessage], summarize: Summarizer | None
) -> tuple[str, str]:
    """The checkpoint for ``middle`` and how it was made.

    The model is asked on the conversation as it stands, then once more with a stricter note;
    an answer that is empty, carries tool-call markup or lacks the headings is never kept. When
    both fail, the digest of the tool history stands in, so a compaction never leaves nothing.
    """
    if summarize is not None:
        prompt = summary_prompt(messages)
        attempts = (
            (prompt, SUMMARY_STATUS_MODEL),
            (f"{prompt}\n\n{RETRY_NOTE}", SUMMARY_STATUS_MODEL_RETRY),
        )
        for text, status in attempts:
            answer = await _ask_for_summary(
                summarize, [*messages, ChatMessage(role="user", content=text)]
            )
            if valid_summary(answer):
                return str(answer), status
            logger.warning("Compaction summary rejected (%s chars)", len(answer or ""))
    return tool_history_digest(middle), SUMMARY_STATUS_DIGEST


_EARLIER_HEADING = "Earlier messages from the person, verbatim (oldest first):"


def _compacted_blocks() -> re.Pattern[str]:
    return re.compile(
        rf"{re.escape(REMINDER_OPEN)}\s*\[Compacted[\s\S]*?{re.escape(REMINDER_CLOSE)}"
    )


def _quoted_requests(bridge: str) -> list[str]:
    """The person's messages an earlier bridge kept verbatim (its ``> `` lines)."""
    if _EARLIER_HEADING not in bridge:
        return []
    lines: list[str] = []
    for line in bridge.split(_EARLIER_HEADING, 1)[1].strip().splitlines():
        if not line.startswith("> "):
            break
        lines.append(line[2:].strip())
    return lines


def _earlier_requests(middle: list[ChatMessage]) -> list[str]:
    """What the person wrote in ``middle``, oldest first, within the verbatim budget.

    A bridge from an earlier compaction (alone, or merged into the message after it) carries the
    messages it kept; they are kept again, and the bridge itself is never quoted.
    """
    pattern = _compacted_blocks()
    texts: list[str] = []
    for message in middle:
        if message.role != "user":
            continue
        content = message.content or ""
        for bridge in pattern.findall(content):
            texts.extend(_quoted_requests(bridge))
        own = pattern.sub("", content).strip()
        if own and not own.startswith(REMINDER_OPEN):
            texts.append(own)
    kept: list[str] = []
    used = 0
    for text in reversed([t for t in texts if t]):
        if used + len(text) > EARLIER_REQUESTS_BUDGET_CHARS:
            break
        kept.append(text)
        used += len(text)
    return list(reversed(kept))


def _current_request(messages: list[ChatMessage]) -> int | None:
    """Index of the turn-opening message the person wrote (engine reminders and steering notes are skipped)."""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if (
            message.role == "user"
            and not (message.content or "").startswith(REMINDER_OPEN)
            and getattr(message, "name", None) != "user_steering"
        ):
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
