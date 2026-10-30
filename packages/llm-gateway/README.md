# `llm-gateway`

Universal model gateway for the Rocket Chat platform: the single choke point for every model call, with per-call credentials, spend reservation and usage attribution.

## Overview

This package implements the `LLMGatewayProtocol` defined in `specifications/interfaces/llm.py` using `litellm`.

### Key Capabilities
- **Universal Provider Routing:** Transparent dispatch to 100+ model providers including OpenRouter, Anthropic, OpenAI, Gemini, Bedrock, and local models.
- **Per-call credentials:** when the caller binds a `ModelScope` (organization, requester, agent, credential bindings, attribution) and passes no explicit credentials, the gateway asks its `credential_resolver` (`CredentialResolverProtocol`) for the call's key, base URL and headers: the agent's bound credential for the provider, a refusal under a strict binding or for a withdrawn credential (`CredentialRefusedError`), or the organization's default chain. The resolver lives in the API (`services/model_credentials.py`); this package only calls the protocol.
- **Spend reservation:** before a call the gateway estimates its worst-case cost and asks its `budget_guard` (`BudgetGuardProtocol.reserve`) to set it aside on every spend limit that applies; a full blocking limit refuses the call before anything is bought (`BudgetExceededError`, carried to the turn as a `CallRefusal` on `StreamChunk.refusal` / `ModelExecutionError.refusal`). When the stream ends it settles the reservation with the real cost.
- **Usage attribution:** each call writes one `UsageEvent` through the `usage_sink` with tokens, cost, key source, and the agent, credential, turn, subagent, call role and requester from the `ModelScope`.
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
