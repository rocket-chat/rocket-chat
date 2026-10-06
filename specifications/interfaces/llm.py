"""
Interface definitions for LLM Gateway & BYOK Subsystem.
Defines the contract for routing requests through LiteLLM with multi-tenant keys.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BYOKCredentials:
    provider: str  # "openrouter", "anthropic", "openai", "gemini"
    api_key: str
    api_base: str | None = None
    custom_headers: dict[str, str] = field(default_factory=dict)


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters_json_schema: dict[str, Any]


@dataclass
class ChatMessage:
    role: str  # "system", "user", "assistant", "tool"
    content: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    reasoning: str | None = None


@dataclass
class ModelRequest:
    model: str  # e.g. "openrouter/anthropic/claude-3.7-sonnet"
    messages: list[ChatMessage]
    tools: list[ToolDefinition] | None = None
    temperature: float = 0.2
    max_tokens: int | None = None
    thinking_budget_tokens: int | None = None  # e.g., 4096 for Claude 3.7
    reasoning_effort: str | None = None  # "low" | "medium" | "high" for OpenAI o1/o3
    credentials: BYOKCredentials | None = None
    stream: bool = True


@dataclass
class StreamChunk:
    text_delta: str | None = None
    reasoning_delta: str | None = None
    tool_call_delta: dict[str, Any] | None = None
    is_finished: bool = False
    finish_reason: str | None = None
    token_usage: dict[str, int] | None = None


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
    ) -> None:
        """Records token accounting and cost tracking in PostgreSQL."""
        pass
