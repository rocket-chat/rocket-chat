# Contributor & Developer Guidelines

Welcome to the internal contributor guide for **Rocket Chat**. If you are building new core features, developing plugins, or submitting pull requests to the platform, this directory contains the developer-oriented engineering manuals and protocols.

---

## Contributor Guides

* **[Local Developer Workflow & Monorepo Setup](DEVELOPMENT.md)**  
  Monorepo layout, tool installations (`uv`, `pnpm`), running PostgreSQL with pgvector, spinning up the hot-reloading dev environment, and running automated test suites.

* **[Continuous Integration & Automated Pipelines](CI_CD.md)**  
  GitHub Actions CI workflows, static code analysis (`ruff`, `mypy`), frontend type checks, container image multi-stage builds, and testing against local and Kubernetes sandboxes.

* **[Engineering & Agent Collaboration Rules](../AGENTS.md)**  
  Strict engineering principles: readability, the anti-slop commenting standard, zero quick wins, test-driven verification, and conventional git hygiene.

---

## Fast Contributor Onboarding

```bash
# 1. Install Python tooling
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --dev

# 2. Install Node.js tooling
corepack enable && pnpm install

# 3. Start local PostgreSQL with pgvector and Row-Level Security
docker compose -f deploy/docker-compose.dev.yml up -d

# 4. Run database migrations
uv run alembic upgrade head

# 5. Start Backend (Terminal 1)
DEFAULT_SANDBOX_DRIVER=docker uv run uvicorn api.main:app --reload --port 8000

# 6. Start Web Frontend (Terminal 2)
pnpm --filter web dev
```

For end-user features, installation instructions, and user settings guides, refer to the public [Documentation Website](../doc/).
