# mcp-runner

Runs **command (stdio) MCP servers** in a dedicated pod, away from both the control plane and the agent's sandbox, and carries their JSON-RPC to the control plane over an authenticated WebSocket.

* `mcp_runner.server` - the runner service (FastAPI): `GET /health` and `WS /v1/stdio`, one socket per server process.
* `mcp_runner.process` - starts a command with a clean environment in its own process group, with bounded output.
* `mcp_runner.client` - the transport the API uses (`runner_stdio_transport`); install with the `client` extra.
* `mcp_runner.protocol` - the wire frames and the validation both sides apply.

The runner never receives the control plane's secrets: the server's environment is exactly the variables the administrator configured, plus `PATH`, `HOME` and the package caches (`/cache`, a volume, so `npx`/`uvx` downloads survive restarts). Capacity is bounded in total and per organization, and a process is killed when its socket closes or after `MCP_RUNNER_MAX_LIFETIME_SECONDS`.

## Install and test

```bash
uv sync --dev
uv run pytest tests/unit/test_mcp_runner.py tests/unit/test_mcp_runner_client.py
```

## Run

```bash
MCP_RUNNER_TOKEN=$(openssl rand -hex 24) uv run python -m mcp_runner   # listens on :8100
```

## Environment variables

| Variable | Default | Meaning |
| :--- | :--- | :--- |
| `MCP_RUNNER_TOKEN` | required (>= 16 chars) | Shared secret the control plane presents as a bearer token. |
| `MCP_RUNNER_CACHE_DIR` | `/cache` | Package caches (npm, uv). Mount a volume here. |
| `MCP_RUNNER_WORK_DIR` | `/tmp/mcp-work` | Per-organization scratch directories (also each server's `HOME`). |
| `MCP_RUNNER_MAX_PROCESSES` | `32` | Servers running at once. |
| `MCP_RUNNER_MAX_PROCESSES_PER_ORG` | `8` | Servers one organization may run at once. |
| `MCP_RUNNER_MAX_LIFETIME_SECONDS` | `1800` | A server is stopped after this long. |

The API reaches the runner with `MCP_RUNNER_URL` and the same `MCP_RUNNER_TOKEN`.
