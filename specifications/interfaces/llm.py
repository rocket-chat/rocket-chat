"""
Interface definitions for LLM Gateway & BYOK Subsystem.
Defines the contract for routing requests through LiteLLM with multi-tenant keys.
"""

import re
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class BYOKCredentials:
    provider: str  # "openrouter", "anthropic", "openai", "gemini"
    api_key: str
    api_base: str | None = None
    custom_headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CredentialBinding:
    """An agent's own credential for one provider (``provider_credentials.id``)."""

    provider: str
    credential_id: str


@dataclass(frozen=True)
class ModelScope:
    """Who a model call is made for: the tenant, member and session billed for it.

    Carried in a context variable (not on every request) so every model call - the ReAct
    loop, subagents, titles, summaries, follow-ups - is attributed and budget-checked at the
    single gateway choke point without each call site having to remember to pass it.
    """

    org_id: str
    user_id: str
    session_id: str | None = None
    # The organization's model data rules for this turn. Carried here, and applied by the
    # gateway to every request that does not set them itself, so the auxiliary calls (titles,
    # summaries, follow-ups) obey zero-retention and region pinning like the agent's own.
    zero_retention: bool = False
    region: str = ""
    # The organization's provider preferences per model name or fnmatch pattern (OpenRouter's
    # ``provider`` object: order, allow_fallbacks, ...). Pinning a model to one provider keeps
    # its prompt cache: a request served by another provider starts with nothing cached.
    provider_preferences: tuple[tuple[str, dict[str, object]], ...] = ()
    # The person whose message started the turn. ``user_id`` stays the session's owner (the
    # tenant of record); member keys, member spend limits and agent access use the requester.
    requester_user_id: str | None = None
    # The agent running the turn, and the credentials it is bound to: a call on a bound
    # provider uses the bound key; with ``strict_binding`` a call on any other provider is
    # refused rather than billed elsewhere.
    agent_id: str | None = None
    credential_bindings: tuple[CredentialBinding, ...] = ()
    strict_binding: bool = False
    # Attribution for the usage ledger and spend limits.
    turn_id: str | None = None
    subagent_id: str | None = None
    call_role: str = ""

    @property
    def member_id(self) -> str:
        """The person a member key or a member limit applies to."""
        return self.requester_user_id or self.user_id


current_model_scope: ContextVar[ModelScope | None] = ContextVar("current_model_scope", default=None)


@dataclass
class UsageEvent:
    """One model call's tokens and cost, as persisted in the usage ledger."""

    org_id: str
    user_id: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    session_id: str | None = None
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    # Whose provider key paid: "platform", "org", "user" or "agent" (a key bound to the
    # agent). Spend on a member's own key is theirs, so it counts against no spend limit.
    key_source: str = "platform"
    created_at: int = field(default_factory=lambda: int(time.time()))
    agent_id: str | None = None
    credential_id: str | None = None
    turn_id: str | None = None
    subagent_id: str | None = None
    call_role: str = ""
    requester_user_id: str | None = None


class BudgetExceededError(RuntimeError):
    """A spend limit is reached; raised before any tokens are bought."""

    def __init__(
        self,
        message: str,
        *,
        scope: str,
        limit_usd: float,
        spent_usd: float,
        limit_id: str = "",
        subject: str = "",
        period: str = "",
        resets_at: int | None = None,
    ) -> None:
        super().__init__(message)
        self.scope = scope
        self.limit_usd = limit_usd
        self.spent_usd = spent_usd
        self.limit_id = limit_id
        self.subject = subject
        self.period = period
        self.resets_at = resets_at


class CredentialRefusedError(RuntimeError):
    """The call may not be made with any credential: the agent's bound credential was
    withdrawn, or a strict binding has no credential for this provider."""

    def __init__(self, message: str, *, reason: str, provider: str) -> None:
        super().__init__(message)
        self.reason = reason  # "withdrawn" | "unbound"
        self.provider = provider


@dataclass(frozen=True)
class Reservation:
    """Spend set aside for one call before it is made, settled with the real cost after."""

    id: str
    org_id: str
    amount_usd: float
    # (limit id, subject key, window start) of each counter the estimate was reserved on.
    counters: tuple[tuple[str, str, int], ...] = ()


@dataclass(frozen=True)
class CallRefusal:
    """Why the platform refused a model call before making it, for the client to act on.

    ``kind="budget"``: a spend limit (``limit_id``, ``subject``, ``period``, ``spent_usd``,
    ``amount_usd``, ``resets_at``). ``kind="credential"``: the agent's bound credential was
    withdrawn or a strict binding has none for ``provider`` (``reason``).
    """

    kind: str
    message: str
    limit_id: str = ""
    subject: str = ""
    period: str = ""
    spent_usd: float = 0.0
    amount_usd: float = 0.0
    resets_at: int | None = None
    reason: str = ""
    provider: str = ""

    @classmethod
    def from_error(cls, err: BaseException) -> "CallRefusal | None":
        if isinstance(err, BudgetExceededError):
            return cls(
                kind="budget",
                message=str(err),
                limit_id=err.limit_id,
                subject=err.subject,
                period=err.period,
                spent_usd=err.spent_usd,
                amount_usd=err.limit_usd,
                resets_at=err.resets_at,
            )
        if isinstance(err, CredentialRefusedError):
            return cls(
                kind="credential", message=str(err), reason=err.reason, provider=err.provider
            )
        return None


@dataclass(frozen=True)
class ResolvedCredentials:
    credentials: BYOKCredentials
    key_source: str  # "org", "user" or "agent"
    credential_id: str | None = None


@runtime_checkable
class UsageSinkProtocol(Protocol):
    async def record(self, event: UsageEvent) -> None: ...


@runtime_checkable
class BudgetGuardProtocol(Protocol):
    async def reserve(
        self, scope: ModelScope, *, estimate_usd: float, key_source: str
    ) -> Reservation | None:
        """Set ``estimate_usd`` aside on every limit that applies, atomically, or raise
        ``BudgetExceededError`` naming the limit that has no room. ``None``: nothing applies."""
        ...

    async def settle(self, reservation: Reservation, *, actual_usd: float) -> None:
        """Replace the reservation with the call's real cost."""
        ...


@runtime_checkable
class CredentialResolverProtocol(Protocol):
    async def resolve(self, scope: ModelScope, model: str) -> ResolvedCredentials | None:
        """The member's, else the organization's, own provider key - or ``None`` for platform keys."""
        ...


@dataclass(frozen=True)
class ModelSpec:
    """Capability and token limit metadata for a frontier or custom model."""

    id: str
    name: str = ""
    provider: str = ""
    context_window: int = 128_000
    max_output_tokens: int = 8_192
    supports_reasoning: bool = False
    recommended: bool = False
    custom: bool = False
    # Where ``context_window`` came from: 'override' (registered), 'provider' (the provider's own
    # catalogue), 'litellm', 'family' or 'default' (nothing known: a guess consumers may ignore).
    context_source: str = "default"


@runtime_checkable
class ModelCatalogueProtocol(Protocol):
    """Protocol for querying model specs (context window, max output, reasoning capability)."""

    def get_model_spec(self, model: str) -> ModelSpec | None: ...


# Families that reason before they answer: Claude from 3.7 (every 4.x and 5.x), OpenAI's o-series
# and GPT-5 onwards, Gemini from 2.5, and the dedicated reasoners of other vendors.
_REASONING_PATTERNS = (
    re.compile(r"^o[1-9](-|$)"),
    re.compile(r"claude-3[-.]7"),
    re.compile(r"claude-(opus|sonnet|haiku)-[4-9]"),
    re.compile(r"claude-[4-9]"),
    re.compile(r"gpt-([5-9]|[1-9][0-9])"),
    re.compile(r"gemini-(2[-.]5|[3-9])"),
    re.compile(r"(thinking|reasoner|deepseek-r1|-r1\b|\br1-|qwq|qvq|magistral|grok-[3-9])"),
)


def is_reasoning_model(model: str) -> bool:
    """True if the model is known to perform internal reasoning/thinking steps."""
    name = (model or "").lower().split("/")[-1].split(":", 1)[0]
    return any(pattern.search(name) for pattern in _REASONING_PATTERNS)


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters_json_schema: dict[str, Any]


@dataclass
class AttachmentRef:
    """A file the user attached to their *current* turn.

    The gateway never reads the sandbox: it emits a multimodal content block directly
    from the bytes carried here. Exactly one of ``data`` (binary/image) or ``text``
    (inlined, text-like) is populated, chosen by MIME type. Attachments are attached to
    the current user message only and are never re-sent on later turns, because the
    bytes would otherwise sit in the provider's cached prefix and break prompt caching.
    """

    name: str
    mime: str
    sha256: str
    size: int
    workspace_path: str
    is_image: bool = False
    text: str | None = None
    data: bytes | None = None
    # The stored attachment this came from, so a turn trace can point back at it.
    attachment_id: str | None = None


@dataclass
class ChatMessage:
    role: str  # "system", "user", "assistant", "tool"
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    reasoning: str | None = None
    # The provider's own reasoning blocks (Anthropic-style ``thinking`` with its signature), which
    # such providers require back with the tool calls they led to.
    reasoning_blocks: list[dict[str, Any]] | None = None
    cacheable: bool = False  # Marks a provider prompt-cache breakpoint (stable prefix)
    # Multimodal parts for the current turn only. None on every message
    # restored from history, so a past attachment can never be replayed.
    attachments: list[AttachmentRef] | None = None
    # Who wrote a user message in a shared session; display metadata only, never sent to a model.
    author_user_id: str | None = None
    # On a user message: the agent and model the turn answering it ran with (display only), so
    # the history keeps who answered when the session's selection changes later.
    agent_id: str | None = None
    agent_name: str | None = None
    model: str | None = None
    created_at: int = field(default_factory=lambda: int(time.time()))


@dataclass
class ModelRequest:
    model: str  # e.g. "openrouter/anthropic/claude-3.7-sonnet"
    messages: list[ChatMessage]
    tools: list[ToolDefinition] | None = None
    # None: the provider's own (what a reasoning model gets unless someone chose a value).
    temperature: float | None = 0.2
    max_tokens: int | None = None
    thinking_budget_tokens: int | None = None  # e.g., 4096 for Claude 3.7
    reasoning_effort: str | None = None  # none, minimal, low, medium, high, xhigh or max
    fallback_models: list[str] = field(default_factory=list)
    max_retries: int | None = None
    enable_prompt_cache: bool = True
    credentials: BYOKCredentials | None = None
    stream: bool = True
    # JSON-schema constrained decoding. When set, the gateway passes a
    # ``response_format`` to LiteLLM so the provider returns a JSON object validating
    # against this schema; auxiliary callers then validate with Pydantic instead of
    # scraping prose with regexes.
    response_schema: dict[str, Any] | None = None
    response_schema_name: str = "rocket_response"
    # Constrained tool choice. "none" forces a plain-text completion even when tools are
    # present in the request — used by the terminal synthesis step so a tool-heavy turn
    # that exhausts its budget still produces a real answer instead of another tool call.
    tool_choice: str | None = None
    # Model data governance: the org may require the provider not to retain
    # the request, and may pin the region the request is served from. Carried on the
    # request (rather than read from the app layer inside agent-core) to preserve layering.
    zero_retention: bool = False
    region: str = ""
    timeout: float | None = None


@runtime_checkable
class ModelRouterProtocol(Protocol):
    """Resolves which model to use per *role of work*.

    Roles are ``react`` (the main ReAct loop), ``summarize``, ``suggest`` and
    ``subagent:<role>``. Implemented by ``config_engine.cascading.ModelRouter``; the
    orchestrator depends only on this protocol so the routing policy stays pluggable.
    """

    @property
    def mode(self) -> str:
        """The routing mode in effect (``frontier`` / ``balanced`` / ``economy``)."""
        ...

    def model_for(self, role: str) -> str:
        """The model id to dispatch for ``role``."""
        ...

    def describe(self, role: str) -> str:
        """A human-readable "why this model" explanation for ``role``."""
        ...


@dataclass
class StreamChunk:
    text_delta: str | None = None
    reasoning_delta: str | None = None
    tool_call_delta: dict[str, Any] | None = None
    is_finished: bool = False
    finish_reason: str | None = None
    token_usage: dict[str, int] | None = None
    # What the call cost in USD, set with ``token_usage`` when the stream ends: the amount the
    # provider billed when it reports one (OpenRouter does), else the best known price. Every
    # consumer uses this one figure, so the ledger, the turn telemetry and the provider's
    # invoice agree.
    cost_usd: float | None = None
    # Pieces of the provider's reasoning blocks (thinking text, then its signature).
    reasoning_blocks: list[dict[str, Any]] | None = None
    # Set (with `finish_reason="error"`) when the provider call failed; consumers must treat
    # it as a failure rather than as model output.
    error: str | None = None
    # Set with ``error`` when the platform refused the call itself (a spend limit, a withdrawn
    # credential): what a client needs to explain it and offer the way out.
    refusal: CallRefusal | None = None


class ModelExecutionError(RuntimeError):
    """The model provider call failed (reported by a `StreamChunk` carrying `error`)."""

    def __init__(self, message: str, refusal: CallRefusal | None = None) -> None:
        super().__init__(message)
        self.refusal = refusal


# `StreamChunk.finish_reason` of a call refused because the request exceeds the model's window.
CONTEXT_WINDOW_EXCEEDED = "context_window_exceeded"


class ContextWindowExceededError(ModelExecutionError):
    """The request was larger than the model's context window; compacting it may help."""


class LLMGatewayProtocol(ABC):
    """
    Contract for managing model dispatches, provider fallbacks,
    token cost accounting, and BYOK credential injection.
    """

    @abstractmethod
    def chat_stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[StreamChunk]:
        """
        Dispatches streaming completion request to LiteLLM.
        Separates thought/reasoning chunks from response tokens and tool calls.
        """
        pass

    @abstractmethod
    async def check_budget(
        self,
        tenant_org_id: str,
        tenant_user_id: str,
    ) -> bool:
        """
        Verifies that current monthly spend does not exceed configured limits.
        """
        pass

    @abstractmethod
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
        """Records token accounting and cost tracking in PostgreSQL."""
        pass
