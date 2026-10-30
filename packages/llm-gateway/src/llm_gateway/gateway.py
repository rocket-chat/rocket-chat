"""LiteLLM Gateway implementing LLMGatewayProtocol with BYOK routing and reasoning extraction."""

import asyncio
import base64
import dataclasses
import logging
import os
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping
from fnmatch import fnmatch
from typing import Any

import litellm

from llm_gateway.errors import DataGovernanceError
from llm_gateway.pricing import estimate_cost_usd
from llm_gateway.reasoning import (
    _BLOCKS,
    _CONTENT,
    ReasoningStreamFilter,
    reasoning_replay,
    sanitize_replayed_reasoning,
)
from llm_gateway.tokens import estimated_usage, request_tokens
from specifications.interfaces.llm import (
    CONTEXT_WINDOW_EXCEEDED,
    AttachmentRef,
    BudgetGuardProtocol,
    BYOKCredentials,
    CallRefusal,
    ChatMessage,
    CredentialResolverProtocol,
    LLMGatewayProtocol,
    ModelRequest,
    ModelScope,
    Reservation,
    StreamChunk,
    ToolDefinition,
    UsageEvent,
    UsageSinkProtocol,
    current_model_scope,
)

# Completion tokens a spend reservation assumes when the request sets no ``max_tokens``: the
# reservation is a worst case that settle corrects, so it errs on the high side.
DEFAULT_ESTIMATED_COMPLETION_TOKENS = 8192

# Substrings of the model ids the platform routes to that accept image input. Kept as an
# explicit allowlist rather than a denylist: guessing wrong in the permissive direction
# would send image blocks to a text-only model and fail the whole turn.
_VISION_MODEL_MARKERS = (
    "claude-3",
    "claude-4",
    "gpt-4o",
    "gpt-4-turbo",
    "gpt-4.1",
    "gpt-5",
    "gemini",
    "pixtral",
    "llava",
    "qwen-vl",
    "qwen2-vl",
    "llama-3.2-vision",
    "llama-4",
)

# Provider resilience is delegated entirely to LiteLLM: retries, backoff and
# fail-over are configured per request rather than reimplemented here.


logger = logging.getLogger(__name__)


def _current_turn_start(messages: list[ChatMessage]) -> int:
    """Index of the latest message the person wrote (the tool loop of the turn follows it)."""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.role == "user" and not (message.content or "").startswith("<system-reminder>"):
            return index
    return 0


def _attach_reasoning(
    entry: dict[str, Any], message: ChatMessage, replay: str | None, *, in_current_turn: bool
) -> None:
    if replay == _BLOCKS and message.reasoning_blocks:
        entry["thinking_blocks"] = message.reasoning_blocks
    elif replay == _CONTENT and in_current_turn and message.reasoning and message.tool_calls:
        replayed = sanitize_replayed_reasoning(message.reasoning)
        if replayed:
            entry["reasoning_content"] = replayed


def _provider_preferences(
    model: str, configured: tuple[tuple[str, dict[str, object]], ...]
) -> dict[str, object]:
    """The first configured preferences whose pattern names ``model`` (with or without the
    gateway prefix), else none."""
    name = (model or "").lower()
    bare = name.split("/", 1)[1] if name.startswith("openrouter/") else name
    for pattern, preferences in configured:
        wanted = pattern.lower()
        if fnmatch(name, wanted) or fnmatch(bare, wanted):
            return dict(preferences)
    return {}


def _merge_extra_body(kwargs: dict[str, Any], extra: dict[str, Any]) -> None:
    """Add ``extra`` to the request body options, merging the provider object key by key."""
    if not extra:
        return
    body = kwargs.setdefault("extra_body", {})
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(body.get(key), dict):
            body[key] = {**body[key], **value}
        else:
            body[key] = value


def misplaced_system_messages(messages: list[ChatMessage]) -> int:
    """How many ``system`` messages come after the first non-system message."""
    started = False
    count = 0
    for message in messages:
        if message.role != "system":
            started = True
        elif started:
            count += 1
    return count


# Self-hosted providers keep nothing and run where the organization put them.
# The environment variable that carries each provider's deployment-wide key, the one used when
# neither a member nor the organization stored a key of their own.
PROVIDER_KEY_ENV_VARS: dict[str, str] = {
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "azure": "AZURE_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "groq": "GROQ_API_KEY",
}

_SELF_HOSTED_PROVIDERS = frozenset({"ollama", "ollama_chat", "hosted_vllm", "vllm", "lm_studio"})
_ZERO_RETENTION_PROVIDERS = frozenset({"openrouter"}) | _SELF_HOSTED_PROVIDERS
_REGION_PROVIDERS = (
    frozenset({"vertex_ai", "google", "gemini", "bedrock", "bedrock_converse", "aws"})
    | _SELF_HOSTED_PROVIDERS
)


class LiteLLMGateway(LLMGatewayProtocol):
    """Universal LLM router managing provider dispatch, credential injection, and token streaming."""

    # This gateway records every call's usage itself, once, at the point the stream ends -
    # including title, summary and follow-up calls that never reported it before - so callers
    # must not also call `record_usage`.
    records_usage = True

    def __init__(self) -> None:
        self.usage_sink: UsageSinkProtocol | None = None
        self.budget_guard: BudgetGuardProtocol | None = None
        self.credential_resolver: CredentialResolverProtocol | None = None
        self._usage_ledger: list[dict[str, Any]] = []
        env_retries = os.getenv("ROCKET_MODEL_MAX_RETRIES")
        self._max_retries: int = int(env_retries) if env_retries and env_retries.isdigit() else 2
        self._prompt_cache_enabled: bool = os.getenv("ROCKET_PROMPT_CACHE", "true").lower() not in (
            "false",
            "0",
            "no",
        )
        self._sanitize_environment_keys()

    def _format_request_messages(
        self, messages: list[ChatMessage], model: str, explicit_cache: bool = False
    ) -> list[dict[str, Any]]:
        if misplaced := misplaced_system_messages(messages):
            # Several provider translations move every system message into the system prompt,
            # out of its place in the conversation; per-turn notes belong in a user reminder.
            logger.warning(
                "%d system message(s) after the conversation started in a request to %s",
                misplaced,
                model,
            )
        formatted: list[dict[str, Any]] = []
        replay = reasoning_replay(model)
        turn_start = _current_turn_start(messages)
        for position, msg in enumerate(messages):
            entry: dict[str, Any] = {"role": msg.role}
            if msg.role == "assistant":
                _attach_reasoning(entry, msg, replay, in_current_turn=position >= turn_start)
            if msg.attachments:
                entry["content"] = self._format_attachment_content(msg, model, explicit_cache)
            elif msg.content is not None:
                if explicit_cache and msg.cacheable:
                    # Anthropic-style cache breakpoint on a stable prefix block.
                    entry["content"] = [
                        {
                            "type": "text",
                            "text": msg.content,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]
                else:
                    entry["content"] = msg.content
            if msg.tool_call_id is not None:
                entry["tool_call_id"] = msg.tool_call_id
            if msg.tool_calls is not None:
                entry["tool_calls"] = msg.tool_calls
            formatted.append(entry)
        return formatted

    @staticmethod
    def _supports_vision(model: str) -> bool:
        """Whether ``model`` accepts ``image_url`` content blocks."""
        normalized = (model or "").lower()
        return any(marker in normalized for marker in _VISION_MODEL_MARKERS)

    def _format_attachment_content(
        self, msg: ChatMessage, model: str, explicit_cache: bool
    ) -> list[dict[str, Any]]:
        """Build multimodal content blocks for the current user turn.

        Text-like attachments are inlined as their own labelled text blocks; binary image
        attachments become ``image_url`` blocks. A model that cannot see images receives an
        explicit degradation note instead of a block the provider would reject, so the
        failure is visible rather than a dropped file.
        """
        attachments: list[AttachmentRef] = list(msg.attachments or [])
        blocks: list[dict[str, Any]] = []
        if msg.content:
            text_block: dict[str, Any] = {"type": "text", "text": msg.content}
            if explicit_cache and msg.cacheable:
                text_block["cache_control"] = {"type": "ephemeral"}
            blocks.append(text_block)

        for att in attachments:
            if att.is_image:
                continue
            if att.text:
                blocks.append(
                    {
                        "type": "text",
                        "text": (
                            f"[Attached file: {att.name} ({att.mime}, {att.size} bytes)]\n"
                            f"{att.text}"
                        ),
                    }
                )

        images = [att for att in attachments if att.is_image and att.data]
        if images and not self._supports_vision(model):
            skipped = ", ".join(att.name for att in images)
            blocks.append(
                {
                    "type": "text",
                    "text": (
                        "[Attachment notice] The selected model cannot process image input, "
                        f"so {len(images)} image attachment(s) were not sent: {skipped}. "
                        "Switch to a vision-capable model to analyse them."
                    ),
                }
            )
        else:
            for att in images:
                encoded = base64.b64encode(att.data or b"").decode("ascii")
                blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{att.mime};base64,{encoded}"},
                    }
                )

        if not blocks:
            # Only text-like attachments were present and the service inlined them, so the
            # content is non-null; a defensive empty string keeps the message well-formed.
            blocks.append({"type": "text", "text": msg.content or ""})
        return blocks

    def _format_request_tools(
        self, tools: list[ToolDefinition] | None, explicit_cache: bool = False
    ) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        formatted = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters_json_schema,
                },
            }
            for t in tools
        ]
        if explicit_cache:
            # Tool schemas are static for a session; cache them alongside the prefix.
            formatted[-1]["cache_control"] = {"type": "ephemeral"}
        return formatted

    @staticmethod
    def _supports_explicit_cache(model: str) -> bool:
        """Whether the provider accepts explicit `cache_control` markers.

        Anthropic-compatible models support block-level cache control. Other
        providers (OpenAI, DeepSeek, Gemini, OpenRouter passthrough) rely on
        automatic prefix caching, which still benefits from a stable prefix and
        is therefore not disabled — only the explicit markers are omitted.
        """
        normalized = (model or "").lower()
        return "claude" in normalized or "anthropic" in normalized

    @staticmethod
    def _sanitize_header_value(val: str) -> str:
        """Strip control characters and newlines that cause HTTP header injection errors."""
        return "".join(ch for ch in str(val) if ch not in "\r\n\x00").strip()

    def _sanitize_environment_keys(self) -> None:
        """Ensure standard LLM provider API keys in os.environ have no trailing newlines or control chars."""
        key_names = PROVIDER_KEY_ENV_VARS.values()
        for key in key_names:
            val = os.getenv(key)
            if val is not None:
                cleaned = self._sanitize_header_value(val)
                os.environ[key] = cleaned
                if key == "OPENROUTER_API_KEY":
                    litellm.openrouter_key = cleaned
                elif key == "ANTHROPIC_API_KEY":
                    litellm.anthropic_key = cleaned
                elif key == "OPENAI_API_KEY":
                    litellm.openai_key = cleaned

    @staticmethod
    def _data_governance_kwargs(model: str, request: ModelRequest) -> dict[str, Any]:
        """Translate an org's model-data policy into provider kwargs.

        Zero-retention and region are org policy, not per-provider trivia, so the
        translation is centralised here: attach them as LiteLLM metadata (always safe and
        visible to callbacks), then add the provider-native knob where one exists
        (OpenRouter data-collection denial; Vertex/Bedrock region pinning).
        """
        if not request.zero_retention and not request.region:
            return {}

        provider = model.split("/", 1)[0].lower() if "/" in model else model.lower()
        # A rule the provider cannot be asked to follow is a refusal, not a metadata tag nobody
        # reads: the call would otherwise go out, and the organization would believe it was bound.
        if request.zero_retention and provider not in _ZERO_RETENTION_PROVIDERS:
            raise DataGovernanceError(
                f"Zero retention is required, but it cannot be requested from '{provider}'. "
                "Use an OpenRouter or self-hosted model, or turn the setting off "
                "(Settings > Governance)."
            )
        if request.region and provider not in _REGION_PROVIDERS:
            raise DataGovernanceError(
                f"A region ('{request.region}') is required, but it cannot be requested from "
                f"'{provider}'. Use a Vertex AI, Bedrock or self-hosted model, or clear the "
                "setting (Settings > Governance)."
            )
        meta: dict[str, Any] = {}
        if request.zero_retention:
            meta["zero_retention"] = True
        if request.region:
            meta["region"] = request.region
        kwargs: dict[str, Any] = {"metadata": meta}

        if request.zero_retention and provider == "openrouter":
            kwargs["extra_body"] = {"provider": {"data_collection": "deny"}}
        if request.region:
            if provider in ("vertex_ai", "google", "gemini"):
                kwargs["vertex_location"] = request.region
            elif provider in ("bedrock", "bedrock_converse", "aws"):
                kwargs["aws_region_name"] = request.region
        return kwargs

    def _apply_credentials(
        self, kwargs: dict[str, Any], credentials: BYOKCredentials | None
    ) -> None:
        if not credentials:
            return
        if credentials.api_key:
            kwargs["api_key"] = self._sanitize_header_value(credentials.api_key)
        if credentials.api_base:
            kwargs["api_base"] = self._sanitize_header_value(credentials.api_base)
        if credentials.custom_headers:
            kwargs["extra_headers"] = {
                self._sanitize_header_value(k): self._sanitize_header_value(v)
                for k, v in credentials.custom_headers.items()
            }

    def _completion_kwargs(self, model: str, request: ModelRequest) -> dict[str, Any]:
        """The LiteLLM call arguments for ``request``."""
        self._sanitize_environment_keys()
        explicit_cache = (
            request.enable_prompt_cache
            and self._prompt_cache_enabled
            and self._supports_explicit_cache(model)
        )
        kwargs: dict[str, Any] = {
            "model": model.strip() if model else model,
            "messages": self._format_request_messages(request.messages, model, explicit_cache),
            "temperature": request.temperature,
            "stream": True,
        }
        model_lower = (model or "").lower()
        if request.temperature is None:
            kwargs.pop("temperature")
        elif (
            "claude" in model_lower or "anthropic" in model_lower
        ) and request.thinking_budget_tokens is not None:
            # Anthropic extended thinking requires temperature to be omitted or default 1.0.
            kwargs.pop("temperature", None)
        kwargs.update(self._generation_options(request))
        usage_opts = _usage_options(model)
        if model_lower.startswith("openrouter/"):
            # Usage accounting: OpenRouter then reports what it billed for the call.
            _merge_extra_body(kwargs, {"usage": {"include": True}})
            # OpenRouter takes reasoning effort as its own `reasoning` object, the same for every
            # model behind it. LiteLLM's `reasoning_effort` is checked against its own per-model
            # table instead and refused for any model it does not list ("openrouter does not
            # support parameters: ['reasoning_effort']"), which failed every turn on such a model.
            # Not with a thinking budget: OpenRouter takes an effort or a budget, not both.
            if "thinking" not in kwargs and (effort := kwargs.pop("reasoning_effort", None)):
                _merge_extra_body(kwargs, {"reasoning": {"effort": effort}})
        if "extra_body" in usage_opts:
            kwargs.setdefault("extra_body", {}).update(usage_opts["extra_body"])
        if "stream_options" in usage_opts:
            kwargs["stream_options"] = usage_opts["stream_options"]
        formatted_tools = self._format_request_tools(request.tools, explicit_cache)
        if formatted_tools:
            kwargs["tools"] = formatted_tools
        if request.tool_choice is not None:
            # Forces text over tool calls (e.g. "none" for the terminal synthesis step).
            kwargs["tool_choice"] = request.tool_choice

        self._apply_credentials(kwargs, request.credentials)
        governance = self._data_governance_kwargs(model, request)
        _merge_extra_body(kwargs, governance.pop("extra_body", {}))
        kwargs.update(governance)
        scope = current_model_scope.get()
        if scope is not None and scope.session_id:
            kwargs["user"] = scope.session_id
            if "openrouter" in model_lower or "openai" in model_lower:
                kwargs["prompt_cache_key"] = scope.session_id
            if "openrouter" in model_lower:
                kwargs.setdefault("extra_body", {})["session_id"] = scope.session_id
                kwargs.setdefault("extra_headers", {})["x-session-id"] = scope.session_id
        if scope is not None and "openrouter" in model_lower:
            if preferences := _provider_preferences(model, scope.provider_preferences):
                _merge_extra_body(kwargs, {"provider": preferences})
        return kwargs

    def _generation_options(self, request: ModelRequest) -> dict[str, Any]:
        """Limits, reasoning, resilience and output-format options the request asks for."""
        options: dict[str, Any] = {}
        if request.max_tokens is not None:
            options["max_tokens"] = request.max_tokens
        if request.thinking_budget_tokens is not None:
            options["thinking"] = {
                "type": "enabled",
                "budget_tokens": request.thinking_budget_tokens,
            }
        if request.reasoning_effort is not None:
            options["reasoning_effort"] = request.reasoning_effort

        # Provider resilience is delegated to LiteLLM (num_retries + fallbacks)
        # rather than reimplemented here.
        if request.max_retries is not None:
            options["num_retries"] = request.max_retries
        elif self._max_retries is not None:
            options["num_retries"] = self._max_retries
        if request.fallback_models:
            options["fallbacks"] = list(request.fallback_models)

        if request.response_schema is not None:
            # Constrained decoding: LiteLLM translates this into the provider-native
            # json_schema response format where supported, and best-effort JSON mode
            # otherwise. The caller still validates with Pydantic (structured.py).
            options["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.response_schema_name,
                    "schema": request.response_schema,
                    "strict": True,
                },
            }
        return options

    @staticmethod
    def _provider_error_chunks(model: str, error: Exception) -> list[StreamChunk]:
        """A failure the person can act on, as an error: never as the model's answer.

        Reported as model output it was stored as the reply, titled the session and taught the
        next turn that the assistant had said it.
        """
        provider = model.split("/")[0] if "/" in model else "the provider"
        if isinstance(error, litellm.exceptions.NotFoundError):
            hint = (
                f"The model '{model}' was not found at {provider}: check its name in the agent "
                "or in Settings > Models."
            )
        else:
            hint = (
                f"{provider} refused the credentials: add or fix the provider key in Settings > "
                "Models (or the deployment's key, e.g. OPENROUTER_API_KEY)."
            )
        return [StreamChunk(is_finished=True, finish_reason="error", error=f"{hint} ({error})")]

    @staticmethod
    def _get_val(obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, Mapping):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @classmethod
    def _usage_from_chunk(cls, chunk: Any) -> dict[str, int] | None:
        """Token counts (including prompt-cache reads and writes) the provider reported."""
        usage = cls._get_val(chunk, "usage")
        if not usage:
            return None
        prompt_tokens = cls._get_val(usage, "prompt_tokens", 0) or 0
        completion_tokens = cls._get_val(usage, "completion_tokens", 0) or 0
        total_tokens = cls._get_val(usage, "total_tokens", 0) or 0
        usage_dict = {
            "prompt_tokens": int(prompt_tokens),
            "completion_tokens": int(completion_tokens),
            "total_tokens": int(total_tokens),
        }
        cache_read = cls._get_val(usage, "cache_read_input_tokens")
        if cache_read is None:
            cache_read = cls._get_val(usage, "cached_tokens")
        details = cls._get_val(usage, "prompt_tokens_details")
        if cache_read is None and details is not None:
            cache_read = cls._get_val(details, "cached_tokens")
        cache_creation = cls._get_val(usage, "cache_creation_input_tokens")
        if cache_creation is None:
            cache_creation = cls._get_val(usage, "cache_write_tokens")
        if cache_creation is None and details is not None:
            cache_creation = cls._get_val(details, "cache_creation_input_tokens")
            if cache_creation is None:
                cache_creation = cls._get_val(details, "cache_write_tokens")
        if cache_read is not None:
            usage_dict["cache_read_tokens"] = int(cache_read)
        if cache_creation is not None:
            usage_dict["cache_creation_tokens"] = int(cache_creation)
        return usage_dict

    @classmethod
    def _billed_cost(cls, chunk: Any) -> float | None:
        """The cost the provider billed for the call, when it reports one (OpenRouter's
        ``usage.cost`` with usage accounting on)."""
        cost = cls._get_val(cls._get_val(chunk, "usage"), "cost")
        try:
            return float(cost) if cost is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _filtered_chunks(
        pieces: Iterable[tuple[str, str]],
    ) -> Iterator[StreamChunk]:
        """Reasoning/text pieces from the ``<think>`` filter as stream chunks."""
        for stream_type, token_text in pieces:
            if stream_type == "reasoning":
                yield StreamChunk(reasoning_delta=token_text)
            else:
                yield StreamChunk(text_delta=token_text)

    @classmethod
    def _delta_chunks(
        cls, delta: Any, reasoning_filter: ReasoningStreamFilter
    ) -> Iterator[StreamChunk]:
        # 1. Provider-native reasoning fields (e.g. DeepSeek / Anthropic thinking)
        provider_reasoning = getattr(delta, "reasoning_content", None) or getattr(
            delta, "thinking", None
        )
        if provider_reasoning:
            yield StreamChunk(reasoning_delta=provider_reasoning)
        blocks = getattr(delta, "thinking_blocks", None)
        if blocks:
            yield StreamChunk(reasoning_blocks=[dict(block) for block in blocks])

        # 2. Text tokens, filtered for in-band <think>...</think> tags
        content = getattr(delta, "content", None)
        if content:
            yield from cls._filtered_chunks(reasoning_filter.feed(content))

        # 3. Tool call streaming deltas
        for tool_call in getattr(delta, "tool_calls", None) or []:
            func = getattr(tool_call, "function", None)
            func_dict = (
                {
                    "name": getattr(func, "name", None),
                    "arguments": getattr(func, "arguments", None),
                }
                if func
                else None
            )
            yield StreamChunk(
                tool_call_delta={
                    "index": getattr(tool_call, "index", 0),
                    "id": getattr(tool_call, "id", None),
                    "type": getattr(tool_call, "type", "function"),
                    "function": func_dict,
                }
            )

    async def _stream_model(
        self,
        model: str,
        request: ModelRequest,
    ) -> AsyncIterator[StreamChunk]:
        kwargs = self._completion_kwargs(model, request)
        reasoning_filter = ReasoningStreamFilter()
        try:
            response = await litellm.acompletion(**kwargs)
        except (
            litellm.exceptions.AuthenticationError,
            litellm.exceptions.NotFoundError,
        ) as auth_err:
            for error_chunk in self._provider_error_chunks(model, auth_err):
                yield error_chunk
            return

        latest_usage: dict[str, int] | None = None
        billed: float | None = None
        last_finish_reason: str | None = None
        ait = aiter(response)
        idle_timeout = float(getattr(request, "timeout", None) or 90.0)
        while True:
            try:
                chunk = await asyncio.wait_for(anext(ait), timeout=idle_timeout)
            except StopAsyncIteration:
                break
            except TimeoutError:
                for pending in self._filtered_chunks(reasoning_filter.flush()):
                    yield pending
                yield StreamChunk(
                    is_finished=True,
                    finish_reason="error",
                    error=f"LLM stream idle timeout after {int(idle_timeout)}s with no chunks received",
                    token_usage=latest_usage,
                    cost_usd=billed,
                )
                return

            # Providers put usage on the finishing chunk, on a trailing chunk without choices
            # or on an ordinary content chunk; keep the latest wherever it came.
            usage_dict = self._usage_from_chunk(chunk)
            if usage_dict:
                latest_usage = usage_dict
                billed = self._billed_cost(chunk)
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue

            choice = choices[0]
            delta = getattr(choice, "delta", None)
            if delta is not None:
                for delta_chunk in self._delta_chunks(delta, reasoning_filter):
                    yield delta_chunk

            fr = getattr(choice, "finish_reason", None)
            if fr:
                last_finish_reason = fr

        for pending in self._filtered_chunks(reasoning_filter.flush()):
            yield pending
        yield StreamChunk(
            is_finished=True,
            finish_reason=last_finish_reason or "stop",
            token_usage=latest_usage,
            cost_usd=billed,
        )

    async def chat_stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a completion.

        Retries and provider fail-over are delegated to LiteLLM (``num_retries``,
        ``fallbacks``), which implements backoff and retryable-error
        classification. This wrapper only converts an exhausted/fatal failure into
        a user-visible error chunk. LiteLLM does not retry after tokens have been
        streamed, so partial output can never be duplicated.

        It is also the single choke point for tenant policy: the spending caps are checked
        before any provider call is made, the member's or organization's own key is
        substituted for the platform key when one applies, and the call's tokens and cost
        are written to the usage ledger when the stream ends.
        """
        scope = current_model_scope.get()
        if scope is not None and (scope.zero_retention or scope.region):
            request = dataclasses.replace(
                request,
                zero_retention=request.zero_retention or scope.zero_retention,
                region=request.region or scope.region,
            )
        try:
            request, key_source, credential_id = await self._with_credentials(scope, request)
            reservation = await self._reserve(scope, request, key_source)
        except Exception as err:
            yield StreamChunk(
                is_finished=True,
                finish_reason="error",
                error=str(err),
                refusal=CallRefusal.from_error(err),
            )
            return

        usage: dict[str, int] | None = None
        cost: float | None = None
        produced: list[str] = []
        failed = False
        try:
            async for chunk in self._stream_model(request.model, request):
                if chunk.error:
                    failed = True  # a refused call costs nothing; it is not billed
                if chunk.token_usage:
                    usage = chunk.token_usage
                    cost = (
                        chunk.cost_usd
                        if chunk.cost_usd is not None
                        else self._usage_cost(request.model, usage)
                    )
                    chunk = dataclasses.replace(chunk, cost_usd=cost)
                produced.append(_produced_text(chunk))
                yield chunk
        except Exception as err:
            failed = True
            # A failure is a failure, never model output: consumers raise on `error`. A request
            # too large for the window is named, so the caller can compact and retry.
            reason = (
                CONTEXT_WINDOW_EXCEEDED
                if isinstance(err, litellm.exceptions.ContextWindowExceededError)
                else "error"
            )
            yield StreamChunk(is_finished=True, finish_reason=reason, error=str(err))
        finally:
            billed = 0.0
            if not failed or produced or usage:
                billed = await self._record_call(
                    scope, request, usage, "".join(produced), key_source, cost, credential_id
                )
            if reservation is not None:
                await self._settle(reservation, billed)

    async def _with_credentials(
        self, scope: ModelScope | None, request: ModelRequest
    ) -> tuple[ModelRequest, str, str | None]:
        """The request with the member's, the agent's or the organization's key, and whose."""
        if scope is None or request.credentials is not None or self.credential_resolver is None:
            return request, "platform", None
        resolved = await self.credential_resolver.resolve(scope, request.model)
        if resolved is None:
            return request, "platform", None
        return (
            dataclasses.replace(request, credentials=resolved.credentials),
            resolved.key_source,
            resolved.credential_id,
        )

    async def _reserve(
        self, scope: ModelScope | None, request: ModelRequest, key_source: str
    ) -> Reservation | None:
        """Set the call's worst-case cost aside on the spend limits that apply."""
        if scope is None or self.budget_guard is None:
            return None
        prompt = request_tokens(request.model, request.messages, request.tools)
        completion = request.max_tokens or DEFAULT_ESTIMATED_COMPLETION_TOKENS
        estimate = self._cost_usd(request.model, prompt, completion)
        return await self.budget_guard.reserve(scope, estimate_usd=estimate, key_source=key_source)

    async def _settle(self, reservation: Reservation, billed: float) -> None:
        guard = self.budget_guard
        if guard is None:
            return
        try:
            await guard.settle(reservation, actual_usd=billed)
        except Exception:
            # The sweeper releases an unsettled reservation; the turn must not fail for it.
            logger.exception("Could not settle spend reservation %s", reservation.id)

    def _usage_cost(self, model: str, usage: dict[str, int]) -> float:
        return self._cost_usd(
            model,
            int(usage.get("prompt_tokens", 0)),
            int(usage.get("completion_tokens", 0)),
            int(usage.get("cache_read_tokens", 0)),
            int(usage.get("cache_creation_tokens", 0)),
        )

    async def _record_call(
        self,
        scope: ModelScope | None,
        request: ModelRequest,
        usage: dict[str, int] | None,
        produced: str,
        key_source: str,
        billed: float | None = None,
        credential_id: str | None = None,
    ) -> float:
        """Write the call to the ledger; returns what it cost."""
        if scope is None or self.usage_sink is None:
            return float(billed or 0.0)
        if usage:
            prompt = int(usage.get("prompt_tokens", 0))
            completion = int(usage.get("completion_tokens", 0))
            cache_read = int(usage.get("cache_read_tokens", 0))
            cache_creation = int(usage.get("cache_creation_tokens", 0))
        else:
            prompt, completion = estimated_usage(
                request.model, request.messages, request.tools, produced
            )
            cache_read = 0
            cache_creation = 0
        cost = (
            billed
            if billed is not None
            else self._cost_usd(request.model, prompt, completion, cache_read, cache_creation)
        )
        try:
            await self.usage_sink.record(
                UsageEvent(
                    org_id=scope.org_id,
                    user_id=scope.user_id,
                    session_id=scope.session_id,
                    model=request.model,
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    cache_read_tokens=cache_read,
                    cache_creation_tokens=cache_creation,
                    cost_usd=cost,
                    key_source=key_source,
                    agent_id=scope.agent_id,
                    credential_id=credential_id,
                    turn_id=scope.turn_id,
                    subagent_id=scope.subagent_id,
                    call_role=scope.call_role,
                    requester_user_id=scope.requester_user_id,
                )
            )
        except Exception:
            # Accounting must never fail the user's turn; the failure is loud in the logs.
            logger.exception("Could not record model usage for %s", request.model)
        return float(cost)

    @staticmethod
    def _cost_usd(
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> float:
        """The provider's published price from LiteLLM, else the conservative estimate.

        A gateway's name for a model (``openrouter/deepseek/deepseek-v4-pro``) is often absent
        from LiteLLM's table while the model itself (``deepseek/deepseek-v4-pro``) is listed:
        without that second lookup every such call was priced at a family's old rate card.
        """
        names = [model]
        if (model or "").lower().startswith("openrouter/"):
            names.append(model.split("/", 1)[1])
        for name in names:
            try:
                prompt_cost, completion_cost = litellm.cost_per_token(
                    model=name,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cache_read_input_tokens=cache_read_tokens,
                    cache_creation_input_tokens=cache_creation_tokens,
                )
            except Exception:
                continue
            total = float(prompt_cost) + float(completion_cost)
            if total > 0:
                return round(total, 6)
        return estimate_cost_usd(
            model,
            prompt_tokens,
            completion_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
        )

    def _estimate_cost_usd(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> float:
        """Estimate token cost based on model family and token usage."""
        return estimate_cost_usd(
            model,
            prompt_tokens,
            completion_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
        )

    def get_user_usage_summary(self, tenant_user_id: str) -> dict[str, Any]:
        """Aggregate personal usage for a single developer."""
        user_entries = [e for e in self._usage_ledger if e.get("tenant_user_id") == tenant_user_id]
        total_prompt = sum(e.get("prompt_tokens", 0) for e in user_entries)
        total_completion = sum(e.get("completion_tokens", 0) for e in user_entries)
        total_cost = sum(e.get("cost_usd", 0.0) for e in user_entries)
        total_cache_read = sum(e.get("cache_read_tokens", 0) for e in user_entries)

        return {
            "tenant_user_id": tenant_user_id,
            "total_tokens": total_prompt + total_completion,
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_cost_usd": round(total_cost, 4),
            "cache_read_tokens": total_cache_read,
            "recent_turns_count": len(user_entries),
        }

    async def check_budget(
        self,
        tenant_org_id: str,
        tenant_user_id: str,
    ) -> bool:
        """Never refuses up front: spend limits hold per call, through ``budget_guard``'s
        reservation in ``chat_stream``, which knows the call's worst-case cost."""
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
        cost = self._estimate_cost_usd(
            model,
            prompt_tokens,
            completion_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
        )
        self._usage_ledger.append(
            {
                "tenant_org_id": tenant_org_id,
                "tenant_user_id": tenant_user_id,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cache_read_tokens": cache_read_tokens,
                "cache_creation_tokens": cache_creation_tokens,
                "cost_usd": cost,
            }
        )


def _usage_options(model: str) -> dict[str, Any]:
    """Ask for the usage block where the provider only sends it on request (OpenAI-compatible
    APIs); LiteLLM reports it natively for the others, which reject the option."""
    try:
        supported = litellm.get_supported_openai_params(model=model) or []
    except Exception:
        return {}
    return {"stream_options": {"include_usage": True}} if "stream_options" in supported else {}


def _produced_text(chunk: StreamChunk) -> str:
    """What a chunk adds to the reply, tool-call arguments included: they are output tokens."""
    arguments = ((chunk.tool_call_delta or {}).get("function") or {}).get("arguments") or ""
    return (chunk.text_delta or "") + (chunk.reasoning_delta or "") + arguments
