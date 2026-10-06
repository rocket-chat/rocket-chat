# Local Developer Workflow

This guide covers active daily development on Rocket Chat, including monorepo tooling, hot-reloading development servers, database provisioning, and verification testing.

---

## 1. Monorepo Structure

Rocket Chat is organized as a unified monorepo managed with **`uv`** (Python) and **`pnpm`** (TypeScript):

```text
rocket-chat/
├── apps/
│   ├── api/                     # FastAPI ASGI Control Plane & Service Host
│   └── web/                     # Next.js 15 Web Mission Control
├── packages/
│   ├── agent-core/              # Custom Async ReAct Orchestrator & Tool Registry
│   ├── config-engine/           # 4-tier Hierarchical Config & Policy Engine
│   ├── git-engine/              # Git operations, commit signing, & co-authors
│   ├── llm-gateway/             # LiteLLM universal provider router & BYOK cipher
│   ├── sandbox-driver/          # Docker & Kubernetes dual sandbox drivers
│   └── slack-assistant/         # Embedded Slack Bolt Socket Mode assistant
├── deploy/
│   ├── docker/                  # Multi-stage production Dockerfiles & entrypoints
│   ├── docker-compose.prod.yml  # Production multi-container stack
│   ├── docker-compose.dev.yml   # Minimal PostgreSQL development stack
│   └── helm/platform/           # Production Kubernetes Helm chart
├── doc/                         # MkDocs Material documentation source files
├── tests/
│   ├── unit/                    # Fast isolated component unit tests
│   └── integration/             # Multi-component & end-to-end integration tests
└── specifications/
    └── interfaces/              # Strict Python protocol interfaces (Source of Truth)
```

---

## 2. Tooling Installation

### Python Tooling (`uv`)
Install Astral's `uv` package manager:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Sync all backend packages and developer dependencies:

```bash
uv sync --dev
```

### Node.js Tooling (`pnpm`)
Ensure Node.js 20+ is active, then enable `pnpm`:

```bash
corepack enable
corepack prepare pnpm@9 --activate
pnpm install
```

---

## 3. Starting the Local Development Stack

For active daily development, you do **not** need to build application Docker containers. Instead, run PostgreSQL in Docker and run the Backend and Frontend with live hot-reloading.

### Step 1: Start Local PostgreSQL (with pgvector and RLS)
```bash
docker compose -f deploy/docker-compose.dev.yml up -d
```
This initializes a PostgreSQL 16 container with `pgvector` on port 5432 and executes `deploy/docker/init-rls.sql` to establish the `rocket_app` non-superuser role.

### Step 2: Run Database Migrations
Apply the latest Alembic schema migrations:
```bash
uv run alembic upgrade head
```

### Step 3: Start the Backend with Hot-Reload
In terminal 1:
```bash
DEFAULT_SANDBOX_DRIVER=docker uv run uvicorn api.main:app --reload --port 8000
```
FastAPI runs on `http://localhost:8000`, with interactive OpenAPI documentation available at `http://localhost:8000/docs`.

### Step 4: Start the Frontend with Fast Refresh
In terminal 2:
```bash
pnpm --filter web dev
```
Next.js Mission Control runs on `http://localhost:3000` with instant hot-module replacement (HMR).

---

## 4. Running Verification Checks

Adhering to `AGENTS.md`, every PR and major code change must pass all automated verification gates:

### Backend Checks
```bash
# Ruff linting
uv run ruff check .

# Ruff format verification
uv run ruff format --check .

# Strict Mypy type-checking across all packages and API
uv run mypy packages apps/api

# Full unit and integration test suite
uv run pytest tests/
```

### Frontend Checks
```bash
# ESLint
pnpm --filter web lint

# TypeScript compilation check
pnpm --filter web type-check

# Production build verification
pnpm --filter web build
```

### Documentation Checks
```bash
# Preview documentation locally
uv run mkdocs serve

# Build static documentation bundle
uv run mkdocs build
```
