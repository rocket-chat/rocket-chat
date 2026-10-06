# `api`

FastAPI Control Plane and Service Host for Rocket Chat.

## Overview

This application hosts the Rocket Chat API, WebSocket broadcaster, and embedded background workers, including the native Slack Socket Mode Assistant.

## Installation

```bash
uv sync
```

## Running the Server

```bash
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000
```

## Running Tests

```bash
# Integration tests covering embedded workers
uv run pytest tests/integration/test_slack_interoperability.py -v
```

## Environment Variables

- `SLACK_BOT_TOKEN`: (Optional) Slack bot OAuth token (`xoxb-...`) to boot embedded Slack Assistant.
- `SLACK_APP_TOKEN`: (Optional) Slack app-level token (`xapp-...`) enabling Socket Mode.
- `OPENROUTER_API_KEY`: (Optional) OpenRouter API key for LLM gateway inference.
- `ANTHROPIC_API_KEY`: (Optional) Anthropic direct API key.
- `OPENAI_API_KEY`: (Optional) OpenAI direct API key.
