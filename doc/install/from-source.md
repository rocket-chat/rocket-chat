---
title: Run from source
description: Run the API and the cockpit from a checkout, with hot reload, to hack on Rocket Chat or try a change.
---

# Run from source

Use this to change Rocket Chat itself, or to run a branch. For using it, the [Quickstart](/quickstart) is simpler; to run it for a team, use [Docker Compose](/install/docker-compose) or [Kubernetes](/install/kubernetes).

## What you need

| Tool | Version | For |
| :--- | :--- | :--- |
| Python | 3.12+ | The API and the agent engine |
| [`uv`](https://docs.astral.sh/uv/) | latest | Python environments and dependencies |
| Node.js | 22+ | The cockpit |
| [`pnpm`](https://pnpm.io/) | 10+ | The cockpit's dependencies |
| Docker | 24+ | The sandboxes (when you use the Docker driver) and the development database |

## 1. Install

```bash
git clone https://github.com/rocket-chat/rocket-chat.git
cd rocket-chat
uv sync --dev
pnpm install
echo "OPENROUTER_API_KEY=sk-or-..." > .env      # any supported provider key; .env is git-ignored
```

## 2. Start the database (optional for a first run)

Without a database the API keeps sessions in memory, which is enough to look around. For sessions that survive a restart, start the development PostgreSQL (with the `pgvector` extension and row-level security) and migrate it:

```bash
docker compose -f deploy/docker-compose.dev.yml up -d
uv run alembic upgrade head
```

## 3. Run the API and the cockpit

In one terminal, the API with hot reload:

```bash
DEFAULT_SANDBOX_DRIVER=docker uv run uvicorn api.main:app --app-dir apps/api/src --reload --port 8000
```

In another, the cockpit:

```bash
pnpm dev
```

Open **http://localhost:3000**. The API's interactive documentation is at **http://localhost:8000/docs**.

## Switches that help while developing

| Variable | Effect |
| :--- | :--- |
| `DEV_AUTH_BYPASS=true` | Anonymous requests become a local administrator. Ignored in production. |
| `ROCKET_MODEL_STUB=1` | A deterministic stand-in for the model: no provider key and no cost, for working on the interface or the plumbing. |
| `DEFAULT_SANDBOX_DRIVER` | `docker` (real sandboxes), `k8s`, or `fake` (an in-memory sandbox, no Docker needed). |
| `ROCKET_AUTO_CREATE_SCHEMA=true` | Create tables at start-up instead of running migrations. Development and tests only. |

Every variable is in the [configuration reference](/install/configuration).

## Preview these docs

```bash
pnpm run docs:dev        # http://localhost:3333
pnpm run docs:check      # broken links
```

## Contributing a change

Branch from `main`, keep commits small, and run the checks (lint, types, tests, the web build and the browser suite) before you open a pull request. The rules, the commands and the architecture notes for contributors are in the repository: [CONTRIBUTING.md](https://github.com/rocket-chat/rocket-chat/blob/main/CONTRIBUTING.md) and [AGENTS.md](https://github.com/rocket-chat/rocket-chat/blob/main/AGENTS.md).
