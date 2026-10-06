"""LiteLLM Gateway implementing LLMGatewayProtocol with BYOK routing and reasoning extraction."""

from collections.abc import AsyncIterator
from typing import Any

import litellm

from llm_gateway.reasoning import ReasoningStreamFilter
from specifications.interfaces.llm import (
    BYOKCredentials,
    ChatMessage,
    LLMGatewayProtocol,
    ModelRequest,
    StreamChunk,
    ToolDefinition,
)


class LiteLLMGateway(LLMGatewayProtocol):
    """Universal LLM router managing provider dispatch, credential injection, and token streaming."""

    def __init__(self) -> None:
        self._usage_ledger: list[dict[str, Any]] = []

    def _format_request_messages(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for msg in messages:
            entry: dict[str, Any] = {"role": msg.role}
            if msg.content is not None:
                entry["content"] = msg.content
            if msg.tool_call_id is not None:
                entry["tool_call_id"] = msg.tool_call_id
            if msg.tool_calls is not None:
                entry["tool_calls"] = msg.tool_calls
            formatted.append(entry)
        return formatted

    def _format_request_tools(
        self, tools: list[ToolDefinition] | None
    ) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        return [
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

    def _apply_credentials(
        self, kwargs: dict[str, Any], credentials: BYOKCredentials | None
    ) -> None:
        if not credentials:
            return
        kwargs["api_key"] = credentials.api_key
        if credentials.api_base:
            kwargs["api_base"] = credentials.api_base
        if credentials.custom_headers:
            kwargs["extra_headers"] = credentials.custom_headers

    async def chat_stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[StreamChunk]:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": self._format_request_messages(request.messages),
            "temperature": request.temperature,
            "stream": True,
        }
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens

        if request.thinking_budget_tokens is not None:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": request.thinking_budget_tokens,
            }
        if request.reasoning_effort is not None:
            kwargs["reasoning_effort"] = request.reasoning_effort

        formatted_tools = self._format_request_tools(request.tools)
        if formatted_tools:
            kwargs["tools"] = formatted_tools

        self._apply_credentials(kwargs, request.credentials)

        reasoning_filter = ReasoningStreamFilter()
        try:
            response = await litellm.acompletion(**kwargs)
        except (
            litellm.exceptions.AuthenticationError,
            litellm.exceptions.NotFoundError,
        ) as auth_err:
            provider = request.model.split("/")[0] if "/" in request.model else "LLM"
            help_msg = (
                f"⚠️ LLM Provider Error ({provider}): {auth_err}. "
                "Please configure a valid API key (e.g., OPENROUTER_API_KEY in .env, or add your BYOK provider key in Settings > Models)."
            )
            yield StreamChunk(text_delta=help_msg)
            yield StreamChunk(is_finished=True, finish_reason="error")
            return
        except Exception as err:
            yield StreamChunk(text_delta=f"⚠️ Model Execution Error: {err}")
            yield StreamChunk(is_finished=True, finish_reason="error")
            return

        finished = False

        async for chunk in response:
            usage_dict: dict[str, int] | None = None
            if hasattr(chunk, "usage") and chunk.usage:
                usage_dict = {
                    "prompt_tokens": getattr(chunk.usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(chunk.usage, "completion_tokens", 0),
                    "total_tokens": getattr(chunk.usage, "total_tokens", 0),
                }

            choices = getattr(chunk, "choices", None)
            if not choices:
                if usage_dict:
                    yield StreamChunk(is_finished=True, token_usage=usage_dict)
                continue

            choice = choices[0]
            delta = getattr(choice, "delta", None)
            finish_reason = getattr(choice, "finish_reason", None)

            if delta is not None:
                # 1. Provider-native reasoning fields (e.g. DeepSeek / Anthropic thinking)
                provider_reasoning = getattr(delta, "reasoning_content", None) or getattr(
                    delta, "thinking", None
                )
                if provider_reasoning:
                    yield StreamChunk(reasoning_delta=provider_reasoning)

                # 2. Text tokens, filtered for in-band <think>...</think> tags
                content = getattr(delta, "content", None)
                if content:
                    for stream_type, token_text in reasoning_filter.feed(content):
                        if stream_type == "reasoning":
                            yield StreamChunk(reasoning_delta=token_text)
                        else:
                            yield StreamChunk(text_delta=token_text)

                # 3. Tool call streaming deltas
                tool_calls = getattr(delta, "tool_calls", None)
                if tool_calls:
                    for tc in tool_calls:
                        func = getattr(tc, "function", None)
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
                                "index": getattr(tc, "index", 0),
                                "id": getattr(tc, "id", None),
                                "type": getattr(tc, "type", "function"),
                                "function": func_dict,
                            }
                        )

            if finish_reason:
                finished = True
                for stream_type, token_text in reasoning_filter.flush():
                    if stream_type == "reasoning":
                        yield StreamChunk(reasoning_delta=token_text)
                    else:
                        yield StreamChunk(text_delta=token_text)

                yield StreamChunk(
                    is_finished=True,
                    finish_reason=finish_reason,
                    token_usage=usage_dict,
                )

        if not finished:
            for stream_type, token_text in reasoning_filter.flush():
                if stream_type == "reasoning":
                    yield StreamChunk(reasoning_delta=token_text)
                else:
                    yield StreamChunk(text_delta=token_text)
            yield StreamChunk(is_finished=True)

    def _estimate_cost_usd(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Estimate token cost based on model family and token usage."""
        # Standard pricing per million tokens
        m = model.lower()
        if "claude-3-7" in m or "claude-3.7" in m:
            cost = (prompt_tokens * 3.0 / 1_000_000) + (completion_tokens * 15.0 / 1_000_000)
        elif "gpt-4o" in m:
            cost = (prompt_tokens * 2.5 / 1_000_000) + (completion_tokens * 10.0 / 1_000_000)
        elif "deepseek" in m:
            cost = (prompt_tokens * 0.14 / 1_000_000) + (completion_tokens * 0.28 / 1_000_000)
        elif "gemini-2.5" in m or "gemini-2.0" in m:
            cost = (prompt_tokens * 0.10 / 1_000_000) + (completion_tokens * 0.40 / 1_000_000)
        else:
            cost = (prompt_tokens * 1.0 / 1_000_000) + (completion_tokens * 3.0 / 1_000_000)
        return round(cost, 6)

    def set_budget_cap(self, tenant_org_id: str, monthly_budget_usd: float) -> None:
        """Set monthly spending budget cap for an organization."""
        if not hasattr(self, "_budget_caps"):
            self._budget_caps: dict[str, float] = {}
        self._budget_caps[tenant_org_id] = monthly_budget_usd

    def get_org_usage_summary(self, tenant_org_id: str) -> dict[str, Any]:
        """Aggregate total tokens and cost for an organization."""
        org_entries = [e for e in self._usage_ledger if e.get("tenant_org_id") == tenant_org_id]
        total_prompt = sum(e.get("prompt_tokens", 0) for e in org_entries)
        total_completion = sum(e.get("completion_tokens", 0) for e in org_entries)
        total_cost = sum(e.get("cost_usd", 0.0) for e in org_entries)
        budget_cap = getattr(self, "_budget_caps", {}).get(tenant_org_id, 250.0)

        # Breakdown by model
        by_model: dict[str, dict[str, Any]] = {}
        for e in org_entries:
            mod = e.get("model", "unknown")
            if mod not in by_model:
                by_model[mod] = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
            by_model[mod]["prompt_tokens"] += e.get("prompt_tokens", 0)
            by_model[mod]["completion_tokens"] += e.get("completion_tokens", 0)
            by_model[mod]["cost_usd"] = round(by_model[mod]["cost_usd"] + e.get("cost_usd", 0.0), 4)

        return {
            "tenant_org_id": tenant_org_id,
            "total_tokens": total_prompt + total_completion,
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_cost_usd": round(total_cost, 4),
            "monthly_budget_cap_usd": budget_cap,
            "budget_used_percent": round((total_cost / budget_cap) * 100, 2)
            if budget_cap > 0
            else 0,
            "models": by_model,
        }

    def get_user_usage_summary(self, tenant_user_id: str) -> dict[str, Any]:
        """Aggregate personal usage for a single developer."""
        user_entries = [e for e in self._usage_ledger if e.get("tenant_user_id") == tenant_user_id]
        total_prompt = sum(e.get("prompt_tokens", 0) for e in user_entries)
        total_completion = sum(e.get("completion_tokens", 0) for e in user_entries)
        total_cost = sum(e.get("cost_usd", 0.0) for e in user_entries)

        return {
            "tenant_user_id": tenant_user_id,
            "total_tokens": total_prompt + total_completion,
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_cost_usd": round(total_cost, 4),
            "recent_turns_count": len(user_entries),
        }

    async def check_budget(
        self,
        tenant_org_id: str,
        tenant_user_id: str,
    ) -> bool:
        budget_caps = getattr(self, "_budget_caps", {})
        if tenant_org_id in budget_caps:
            cap = budget_caps[tenant_org_id]
            summary = self.get_org_usage_summary(tenant_org_id)
            if summary["total_cost_usd"] >= cap:
                return False
        return True

    async def record_usage(
        self,
        tenant_org_id: str,
        tenant_user_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        cost = self._estimate_cost_usd(model, prompt_tokens, completion_tokens)
        self._usage_ledger.append(
            {
                "tenant_org_id": tenant_org_id,
                "tenant_user_id": tenant_user_id,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": cost,
            }
        )
