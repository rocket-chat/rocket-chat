# `llm-gateway`

Universal Model Gateway and Bring-Your-Own-Key (BYOK) router for the Rocket Chat platform.

## Overview

This package implements the `LLMGatewayProtocol` defined in `specifications/interfaces/llm.py` using `litellm`.

### Key Capabilities
- **Universal Provider Routing:** Transparent dispatch to 100+ model providers including OpenRouter, Anthropic, OpenAI, Gemini, Bedrock, and local models.
- **Dynamic BYOK Injection:** Per-request API key, base URL, and custom header injection for multi-tenant isolation.
- **Reasoning Stream Extraction:** Separates extended thinking / reasoning tokens (`<think>` blocks or provider reasoning streams) cleanly into `StreamChunk.reasoning_delta` without polluting `StreamChunk.text_delta`.
- **Streaming Tool Calls:** Unifies structured tool-calling events across providers.

## Installation

```bash
uv sync
```

## Running Tests

```bash
uv run pytest tests/unit/test_llm_gateway.py -v
```

## Environment Variables

| Variable | Description |
| :--- | :--- |
| `OPENROUTER_API_KEY` | Optional default OpenRouter API key |
| `ANTHROPIC_API_KEY` | Optional default Anthropic API key |
| `OPENAI_API_KEY` | Optional default OpenAI API key |
