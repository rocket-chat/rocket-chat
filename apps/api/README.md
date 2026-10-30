# `api`

FastAPI Control Plane and Service Host for Rocket Chat.

## Overview

This application hosts the Rocket Chat API, WebSocket broadcaster, and embedded background workers, including the native Slack Socket Mode Assistant.

What it owns, beside sessions and turns:

- **People and organizations:** `users` (one identity per person), `memberships` (a role per organization: `owner`, `admin`, `member`, `viewer`), invitations (`org_invites`) and organization switching (`routers/organizations.py`, `services/memberships.py`). A role grants capabilities through the organization's `access` settings (`auth/capabilities.py`).
- **Model credentials:** the organization's and members' labelled provider keys (`provider_credentials`, `routers/credentials.py`), resolved per call by `services/model_credentials.py` (an agent's bindings first, then the organization's chain under `models.member_keys`).
- **Agent access:** who may use each organization agent (`agent_grants`, `services/agent_access.py`), applied to the person who sends each message.
- **Spend limits:** limits on the organization, members, agents and Slack workspaces (`spend_limits`, `routers/spend_limits.py`, `services/spend_limits.py`), enforced by an atomic reservation before each model call (`services/budgets.py`; counters in `spend_counters`, reservations in `spend_reservations`, released by a background sweeper when a call never settles).

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

- `OPENROUTER_API_KEY`: (Optional) OpenRouter API key for LLM gateway inference.
- `ANTHROPIC_API_KEY`: (Optional) Anthropic direct API key.
- `OPENAI_API_KEY`: (Optional) OpenAI direct API key.
- `ROCKET_PROVIDER_CATALOGUE`: (Optional, default `1`) Read model context windows from OpenRouter's public listing; `0` for air-gapped installs.
- `ROCKET_DRAIN_WAIT_SECONDS`: (Optional, default `0`) On shutdown, wait this long for the replica's running turns to finish.
