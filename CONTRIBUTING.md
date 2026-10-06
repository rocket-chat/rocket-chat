# Contributing to Rocket Chat

Thank you for your interest in contributing to **Rocket Chat**! We are building an autonomous AI pair-programming platform that combines sandboxed multi-tenant compute, real-time agent execution, and the **Cosmic Telemetry** avionics cockpit.

---

## 1. Open Source & Licensing Policy

Rocket Chat is **100% Free and Open Source Software** licensed under the **[GNU General Public License v3.0 (GPL-3.0)](LICENSE)**.

### Reciprocal Copyleft & Source Disclosure
In the spirit of Linux and classical open-source software:
- **Share Alike:** Any distributed modification, fork, or derivative work based on Rocket Chat **must be made publicly available** under the terms of the GNU GPL-3.0.
- **Source Attribution:** You must preserve all original copyright notices and provide a clear, prominent reference/link to the upstream source repository ([`https://github.com/rocket-chat/rocket-chat`](https://github.com/rocket-chat/rocket-chat)).
- **No Proprietary Enclosures:** You cannot relicense, enclose, or distribute modified binaries without providing complete, corresponding source code to recipients.

By contributing to this repository, you agree that your contributions are licensed under the GNU General Public License v3.0.

---

## 2. Monorepo Architecture

The repository is structured into modular tiers:

```
rocket-chat/
├── apps/
│   ├── api/                 # FastAPI REST & WebSocket Control Plane (Python 3.12+)
│   └── web/                 # Next.js 15 Cosmic Telemetry Cockpit (TypeScript, Tailwind)
├── packages/
│   ├── agent-core/          # Autonomous reasoning loop & tool executor
│   ├── config-engine/       # Layered configuration & environment resolution
│   ├── git-engine/          # High-performance Libgit2/Dulwich Git driver
│   ├── llm-gateway/         # LiteLLM proxy router with BYOK tenancy
│   ├── sandbox-driver/      # Pluggable Docker & Kubernetes sandboxes
│   └── slack-assistant/     # Slack Socket Mode bot integration
├── specifications/
│   └── interfaces/          # Subsystem protocols & abstract contracts (Single Source of Truth)
├── deploy/                  # Docker Compose, Kind cluster definitions, & Helm charts
├── doc/                     # Living user and architecture documentation (Mintlify)
└── tests/                   # Unit, integration, and end-to-end test suites
```

> [!IMPORTANT]
> **Dependency Rules:** `apps/` may import from `packages/`, but `packages/` must **never** import from `apps/` or create circular dependencies. Core interfaces reside exclusively in `specifications/interfaces/`.

---

## 3. Local Development Setup

### Prerequisites
- **Python 3.12+**
- **`uv`** (Fast Python package and environment manager): [Installation Guide](https://docs.astral.sh/uv/)
- **Node.js 20+** and **`pnpm`** (v9+): `npm install -g pnpm`
- **Docker Engine** (with `/var/run/docker.sock` accessible)
- *(Optional for K8s testing)*: `kind`, `kubectl`, and `helm`

### Installation Steps

1. **Clone the repository:**
   ```bash
   git clone https://github.com/rocket-chat/rocket-chat.git
   cd rocket-chat
   ```

2. **Bootstrap Python workspace dependencies:**
   ```bash
   uv sync
   ```

3. **Bootstrap Web frontend dependencies:**
   ```bash
   pnpm install
   ```

4. **Configure environment:**
   ```bash
   cp .env.example .env  # or initialize .env with your LLM keys
   ```

5. **Run local development services:**
   - **Backend API:**
     ```bash
     uv run uvicorn apps.api.src.api.routes:app --reload --port 8000
     ```
   - **Web Cockpit:**
     ```bash
     pnpm --filter web dev
     ```
   - **Documentation server:**
     ```bash
     pnpm run docs:dev
     ```

---

## 4. Engineering & Code Quality Standards

Contributors must adhere to the engineering guidelines outlined in [`AGENTS.md`](AGENTS.md):

### A. Readability & Single Responsibility (SRP)
- **Code is written for humans first.** Write clear, idiomatic, self-documenting code.
- Prefer explicit variable names over abbreviations.
- Functions must follow the Single Responsibility Principle. If a function exceeds 40 lines or performs multiple distinct tasks, decompose it into private helper functions.

### B. The Anti-Slop Commenting Standard
- **Zero AI Slop:** Do not write redundant narrative comments that merely restate the code syntax.
- **Comment the "WHY", Not the "WHAT":** Reserve comments exclusively for non-obvious architectural decisions, concurrency lock rationale, or library quirk workarounds.

### C. Strict Type Safety
- **Python:** Strict `mypy` enforcement. Do not use `Any` as an escape hatch. Standardize on Generic type variables (`TypeVar`), Pydantic models, or `TypedDict`.
- **TypeScript:** Strict mode enabled (`noImplicitAny: true`). Prohibit `@ts-ignore` or `any` unless wrapping untyped external libraries (with documented justification).

### D. Async Concurrency
- Standardize on `asyncio`.
- Never call blocking synchronous I/O (`time.sleep()`, synchronous `requests`, raw file read/write) inside async paths. Use `asyncio.sleep()`, `httpx.AsyncClient()`, or `anyio.to_thread.run_sync()`.
- Enforce explicit timeouts on all external network calls.

### E. Frontend Avionics Design Language
- All UI components must adhere to the **Cosmic Telemetry & Avionics** system (`#0b0d13` void canvas, `#ff5722` flame accent, `#00e5ff` cyan pulse).
- Avoid standard centered modal dialogs and cookie-cutter chat templates. Use the **Continuous Flight Stream**, **In-Stream Decision Gates**, and **Ignition Console**.

---

## 5. Automated CI Verification

Before submitting any Pull Request, verify that all automated checks pass locally:

```bash
# 1. Backend Linting & Formatting
uv run ruff check .
uv run ruff format --check .

# 2. Python Type Safety
uv run mypy packages apps/api

# 3. Unit & Integration Tests
uv run pytest tests/

# 4. Frontend Type Checking & Linting
pnpm --filter web lint
pnpm --filter web type-check
pnpm --filter web build

# 5. Helm & Documentation
helm lint deploy/helm/platform
pnpm run docs:check
```

---

## 6. Git Hygiene & Branching Strategy

Rocket Chat follows **Trunk-Based Development with Release PRs and SemVer Tags**. Because this repository is a unified monorepo coordinating the backend API, web cockpit, sandbox drivers, and Helm platform chart, all development converges onto `main`.

### A. Feature & Bugfix Workflow
1. **Branch off `main`:**
   ```bash
   git checkout main && git pull origin main
   git checkout -b feat/my-feature-or-fix
   ```
2. **Document changes in `CHANGELOG.md`:**
   Add bullet points under the `## [Unreleased]` section under the appropriate subsystem header (`### Orchestrator`, `### Sandboxes`, `### API Control Plane`, `### Web Cockpit`, `### Configuration & Settings`, `### Git Engine`, or `### Packaging & Helm`).
3. **Follow Conventional Commits:**
   Commit messages must follow the [Conventional Commits](https://www.conventionalcommits.org/) format:
   - `feat: add telemetry streaming compression`
   - `fix: resolve race condition in docker driver port map`
   - `refactor: extract sandbox pool lifecycle helper`
   - `test: add unit coverage for byok token rotation`
   - `docs: update kubernetes quickstart guide`
4. **Co-Authorship Trailers:**
   When pair programming with AI agents or human contributors, include proper `Co-authored-by:` trailers in the commit message:
   ```git
   Co-authored-by: Antigravity <antigravity@google.com>
   ```
5. **Pull Request Submission:**
   Open a PR targeting `main`. Verify all CI gates pass (Backend, Frontend, Helm). Merge using **Squash and Merge** or **Rebase Merge** to keep the `main` commit history clean, linear, and bisect-friendly.

---

### B. Release Preparation Workflow (Release PR)
When preparing a new public version (e.g. `0.2.0`):

1. **Create a release preparation branch:**
   ```bash
   git checkout main && git pull origin main
   git checkout -b chore/release-v0.2.0
   ```
2. **Execute the release devtool:**
   ```bash
   make release-prepare VERSION=0.2.0
   # or: pnpm run version:prepare 0.2.0
   ```
   *This automatically:*
   - Promotes `[Unreleased]` changes in `CHANGELOG.md` to `## [0.2.0] - YYYY-MM-DD`.
   - Re-inserts an empty `## [Unreleased]` template with all subsystem categories.
   - Synchronizes versions across root `package.json`, `apps/web/package.json`, and `deploy/helm/platform/Chart.yaml`.
3. **Validate synchronization:**
   ```bash
   make version-check
   # or: pnpm run version:check
   ```
4. **Submit Release PR:**
   ```bash
   git commit -am "chore(release): prepare v0.2.0"
   git push origin chore/release-v0.2.0
   ```
   Open a PR titled `Release v0.2.0`. Once reviewed and green on CI, merge into `main`.

---

### C. Tagging & Automated Delivery
Once the release PR is merged into `main`:

1. **Tag the release commit:**
   ```bash
   git checkout main && git pull origin main
   git tag -a v0.2.0 -m "Release v0.2.0"
   git push origin v0.2.0
   ```
2. **Automated CI/CD Pipeline Execution (`.github/workflows/release.yml`):**
   - Automatically parses `CHANGELOG.md` using `python3 scripts/release.py notes v0.2.0`.
   - Builds and publishes multi-architecture container images (`ghcr.io/rocket-chat/backend:0.2.0`, `ghcr.io/rocket-chat/frontend:0.2.0`, and `:latest`).
   - Packages and pushes the Helm chart to the OCI registry `oci://ghcr.io/rocket-chat/charts/rocket-chat`.
   - Creates a formal GitHub Release attaching release notes and standalone deployment assets.

---

## 7. Reporting Bugs & Security Disclosures

- **Bug Reports:** Open an issue on GitHub using the bug report template, including logs, reproduction steps, and platform environment (Docker vs. Kubernetes).
- **Security Vulnerabilities:** If you discover a sensitive security vulnerability, please do **not** open a public issue. Email security concerns privately to `security@rocket-chat.dev`.

Thank you for helping propel Rocket Chat forward! 🚀
