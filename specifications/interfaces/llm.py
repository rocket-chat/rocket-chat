"""
Interface definitions for LLM Gateway & BYOK Subsystem.
Defines the contract for routing requests through LiteLLM with multi-tenant keys.
"""

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
    # Whose provider key paid: "platform", "org" or "user". Spend on a member's own key is
    # theirs, so it does not count against the organization's budget.
    key_source: str = "platform"
    created_at: int = field(default_factory=lambda: int(time.time()))


class BudgetExceededError(RuntimeError):
    """A spending cap is reached; raised before any tokens are bought."""

    def __init__(self, message: str, *, scope: str, limit_usd: float, spent_usd: float) -> None:
        super().__init__(message)
        self.scope = scope
        self.limit_usd = limit_usd
        self.spent_usd = spent_usd


@dataclass(frozen=True)
class ResolvedCredentials:
    credentials: BYOKCredentials
    key_source: str  # "org" or "user"


@runtime_checkable
class UsageSinkProtocol(Protocol):
    async def record(self, event: UsageEvent) -> None: ...


@runtime_checkable
class BudgetGuardProtocol(Protocol):
    async def check(self, scope: ModelScope) -> None:
        """Raise ``BudgetExceededError`` if a cap forbids another model call for ``scope``."""
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


def is_reasoning_model(model: str) -> bool:
    """True if the model is known to perform internal reasoning/thinking steps."""
    name = (model or "").lower().split("/")[-1]
    if name.startswith(("o1", "o3", "o4")):
        return True
    if "claude-3-7" in name or "claude-3.7" in name:
        return True
    return any(
        token in name
        for token in ("thinking", "reasoner", "deepseek-r1", "-r1", "r1-", "qwq", "qvq")
    )


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
    temperature: float = 0.2
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


class ModelExecutionError(RuntimeError):
    """The model provider call failed (reported by a `StreamChunk` carrying `error`)."""


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
