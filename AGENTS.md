# Engineering & Agent Collaboration Guidelines (`AGENTS.md`)

The rules for **AI agents** and **human contributors** working on **Rocket Chat**: an agentic software-engineering platform (web cockpit + Slack assistant) that runs an autonomous agent in a per-session sandbox. Every contribution keeps the code readable, the architecture intact and the documentation true.

Read this file first. It points to the two places where depth lives:

| You need | Read |
| :--- | :--- |
| How the system is built, the turn lifecycle, the extension points | [`dev/architecture.md`](dev/architecture.md) |
| Identity, roles, tenant isolation, secrets, the sandbox boundary | [`dev/security-and-tenancy.md`](dev/security-and-tenancy.md) |
| What users and operators see (install, use, administer, operate, API) | the site in `doc/` |
| Contracts between subsystems | `specifications/interfaces/*.py` (the single source of truth) |
| What changed | `CHANGELOG.md` |

---

## 1. Repository at a glance

```
apps/api/            FastAPI control plane (routers, turn engine, bootstrap, services, db)
apps/web/            Next.js 15 / React 19 cockpit + Playwright e2e (apps/web/e2e)
packages/            agent-core, sandbox-driver, llm-gateway, config-engine, git-engine, slack-assistant, mcp-runner
specifications/      Protocols and typed payloads shared by everything (interfaces/)
migrations/          Alembic (the only way the schema changes)
deploy/              Dockerfiles, Compose files, Helm chart (helm/platform), Kind config
doc/                 User documentation site (Mintlify, doc/docs.json)
dev/                 Developer documentation: architecture.md, security-and-tenancy.md
scripts/             Release tool, doc generators and checks, key rotation, DR drill
tests/               unit, integration, security, contract, concurrency, harness (golden tasks)
```

Dependency rule: `apps/` may import `packages/`; `packages/` never import `apps/`; `specifications` imports no workspace package; no cycles. `tests/unit/test_import_boundaries.py` enforces it.

---

## 2. Setup and daily commands

Prerequisites: Python 3.12+ with **`uv`** (never raw `pip`), Node 22+ with **`pnpm`** 10+, Docker. For Kubernetes work also `kind`/`k3d`, `kubectl`, `helm`.

```bash
uv sync --dev && pnpm install                     # dependencies
echo "OPENROUTER_API_KEY=..." > .env                # any LiteLLM-supported provider key (.env is git-ignored)

uv run uvicorn api.main:app --app-dir apps/api/src --reload --port 8000   # API
pnpm dev                                          # cockpit on :3000
docker compose -f deploy/docker-compose.yml up -d # whole evaluation stack (throwaway secrets, dev login)
make dev-cluster / make cluster-down              # Kind + local images + Helm chart
```

Useful switches while developing: `DEV_AUTH_BYPASS=true` (anonymous dev identity, ignored in production), `ROCKET_MODEL_STUB=1` (deterministic stub instead of a real model), `DEFAULT_SANDBOX_DRIVER=docker|k8s|fake`, `ROCKET_AUTO_CREATE_SCHEMA=true` (dev/tests only; deployments run Alembic). Every environment variable is documented in `doc/install/configuration.md`.

### The gates (all must pass before a change is done)

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy packages apps/api
uv run pytest tests/                              # see "database tests" below
pnpm --filter web lint && pnpm --filter web type-check && pnpm --filter web build
pnpm --filter web test:e2e                        # full Playwright suite
helm lint deploy/helm/platform
pnpm run docs:check && uv run python scripts/check_docs.py
```

### Database tests are opt-in and use a disposable database

Without `TEST_DATABASE_URL` the suite runs on in-memory stores and skips PostgreSQL tests (row-level security, migrations, pgvector). The suite deletes tenant rows and creates tables, so it **refuses to start** unless `ROCKET_TEST_DB_IS_DISPOSABLE=1` (or `CI`) is set. **Never point it, migrations or scratch scripts at a developer's working database** (the Compose Postgres on :5432 looks identical). Use a throwaway container on a spare port:

```bash
docker run -d --name rocket-test-pg -e POSTGRES_PASSWORD=postgres -p 55432:5432 pgvector/pgvector:pg16
docker exec rocket-test-pg psql -U postgres -c "CREATE DATABASE rocket_chat" -c "CREATE ROLE rocket_app LOGIN PASSWORD 'rocket_app'"
docker exec rocket-test-pg psql -U postgres -d rocket_chat -c "CREATE EXTENSION vector" \
  -c "GRANT ALL ON SCHEMA public TO rocket_app" \
  -c "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON TABLES TO rocket_app"
export ROCKET_TEST_DB_IS_DISPOSABLE=1
export TEST_DATABASE_URL=postgresql+asyncpg://rocket_app:rocket_app@127.0.0.1:55432/rocket_chat
export ADMIN_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/rocket_chat
uv run pytest tests/
```

### The end-to-end suite also runs against real PostgreSQL

In-memory stores share one object between a writer and a reader; PostgreSQL does not, and some races only show there. CI job `frontend-postgres` runs Playwright against a **migrated** database with forced row-level security. Locally: create the `rocket_app` role and `vector` extension as above, `alembic upgrade head` with `DATABASE_URL`/`ADMIN_DATABASE_URL` on the throwaway database, reset the schema between runs, then run `pnpm --filter web test:e2e` with `E2E_DATABASE_URL` (the `rocket_app` role) and `E2E_ADMIN_DATABASE_URL`. To hunt a flake, loop the suite until it fails, then run the one spec with `--repeat-each=200 --workers=4` and a throwaway spec that logs the WebSocket frames and store calls.

### Live infrastructure tests

`tests/integration/test_k8s_live_cluster.py` (set `ROCKET_K8S_LIVE=1` and a `KUBECONFIG` for a **throwaway** k3d/Kind cluster that has `rocket-chat-sandbox:dev` loaded; `ROCKET_K8S_LIVE_IMAGE` overrides the image) and the Docker interoperability tests exercise real drivers. Create a dedicated cluster with its own kubeconfig file (`--kubeconfig-update-default=false --kubeconfig-switch-context=false`), run, and delete it. Never run them against a shared cluster or switch the user's current kube context. `RUN_LIVE_LLM_TESTS` enables the tests that call a real provider.

---

## 3. Engineering principles

### A. Readability and size
* Code is written for humans first: explicit names over jargon, idiomatic and self-documenting.
* Single Responsibility. `ruff` enforces **cyclomatic complexity ≤ 15, ≤ 60 statements, ≤ 15 branches per function** in every source file. There are **no per-file exemptions** (only `tests/` and `scripts/` are exempt, and `tests/unit/test_complexity_guardrail.py` fails if one is added). If a function does not fit, split it into named private helpers or a small class; if it still does not, the design is wrong. Aim for functions under ~40 lines.
* Start-up work goes in the matching `api/bootstrap/` module, never into `lifespan`; routers stay thin and delegate to `services/` and `turns/`.

### B. The anti-slop commenting standard
No comments that restate the code (`# increment count`, `# fetch user from db`, `# return user`). Comment only the **why**:
1. a non-obvious architectural decision or trade-off;
2. a race-condition mitigation or lock;
3. a workaround for a library, storage-class or upstream quirk;
4. a mathematical or cryptographic formula.

```python
# Pin the pod to its previous node: cloud block storage takes ~90s to re-attach elsewhere.
# Soft affinity, so an unschedulable node falls back to normal scheduling.
affinity = prefer_node(previous_node_name)
```

### C. No quick wins
* No fragile hacks, one-line patches that hide the cause, or shortcuts that create debt. Fix the cause; extend the contract when an abstraction is missing.
* Every feature is built against `specifications/interfaces/`. Change the contract first, then implementations and the tests that pin it.
* **Python:** strict `mypy`; no `Any` escape hatches — use `TypeVar`, Pydantic models, `TypedDict`, `Protocol`. **TypeScript:** `strict` with `noImplicitAny`; no `@ts-ignore` and no `any` unless wrapping an untyped library (say why in a comment).

### D. Context hygiene (the agent's own context)
Tools never emit unbounded output into the session: results are clamped head-and-tail (`clamp_output`), old tool payloads are pruned to receipts past 70% of the window, and compaction at 85% keeps the system prompt, the current request (verbatim), the checklist and the latest whole steps. Reasoning blocks and `tool_call_id` pairing are never dropped.

### E. Concurrency
* `asyncio` throughout. No blocking I/O in async paths (`time.sleep`, sync `requests`, raw file I/O): use `asyncio.sleep`, `httpx.AsyncClient`, `anyio.to_thread.run_sync`. **Every external network call has an explicit timeout** (Docker, Kubernetes, LiteLLM, HTTP).
* Per-turn state lives in `current_execution_ctx` (a `contextvar`), never in mutable callbacks on shared singletons.
* **Never read-modify-write a whole session record.** Use `store.mutate_session(session_id, org, fn)` (row-locked on PostgreSQL). Whole-record `get` + `update` let the title task, the answer and the follow-up erase each other and lost the answer about 1 turn in 12 under load.
* **The connect snapshot loses nothing:** the live queue is opened *before* the session record is read; the turn's closing event is `status_changed` with `final: true`, published after the answer is stored. Do not add a slow step between reading the record, the gates and `last_seq` (see `dev/architecture.md` §5).
* **Every event payload has a type** in `specifications/interfaces/event_payloads.py`; adding a key means changing the type, the handler, the client and the WebSocket documentation in one change. Tests run with `ROCKET_VALIDATE_EVENTS=1` and fail on a violation.

### F. Security and tenancy invariants
Summarised here, specified in `dev/security-and-tenancy.md`: new tables get a migration, `FORCE` row-level security and an RLS test; code outside an HTTP request binds the tenant context before using the database; secrets enter encrypted, leave as fingerprints and never reach the model or the logs; server-side fetches the agent can influence use the SSRF-safe client; enforcement lives in the tool registry, database or driver, never only in a prompt; sessions always start **blank** (the agent clones what it needs).

### G. Long-running sessions

Complex software-engineering turns legitimately run 20+ minutes and 50+ tool calls. Never add hard caps on turn duration, tool count, or ReAct steps; fix the visibility of progress (status events, sidebar indicators) rather than shortening the work.

---

## 4. Technology standards

### Backend (Python 3.12+)
* Dependencies and environments with **`uv`**; formatting and linting with **`ruff`** (`ruff check --fix`, `ruff format`).
* Routes have docstrings and Pydantic response models: they generate the OpenAPI spec and `doc/api/rest.md`.
* The schema changes only through Alembic migrations (`migrations/`); the API refuses to start against an old schema. Migrations run as the schema owner (`ADMIN_DATABASE_URL`), the app as `rocket_app`.

### Frontend (Next.js 15, React 19, TypeScript, Tailwind)
* A modern dark/light pair-programming cockpit with real-time streaming, collapsed-by-default tool steps, `[Tab ⇥]` completion of probable next prompts and system-matching themes. "Rocket" is a visual brand, not the domain of the agent: the default agent is a versatile senior software engineer with a terminal, surgical edits, patches, web lookups and a task checklist.
* Tailwind and **lucide-react**; atomic, composable, accessible components. Every form control is labelled through the shared `components/ui/Field.tsx` (`Field`, `Input`, `Select`, `Textarea`). Client state is Zustand, split into slices under `lib/store/`.
* WCAG 2.1 AA is a test (`e2e/accessibility.spec.ts`, light and dark).

---

## 5. How to add things

The full table is in `dev/architecture.md` §11. In short:

* **Tool** — implement in `packages/agent-core/src/agent_core/tools/builtin/<domain>.py` (or a helper module), register in that group, annotate parameters with `Field(description=…)`, classify it in `permissions.py` (read/edit/command/publish), decide Tier 1/2 and capability gating, update `tests/golden/tool_catalogue.json` and the tool docs in `doc/`.
* **Setting** — add a `SettingSpec` in `packages/config-engine/src/config_engine/registry.py` naming the file that consumes it, **and a behaviour test marked `@pytest.mark.setting("domain.key")`** that changes the value and asserts what the platform then does (`tests/unit/test_every_setting_is_wired.py` fails for a setting without one); regenerate `doc/administer/settings-reference.md`. A setting that is stored and shown but changes nothing is a bug, not a feature.
* **Route** — new router module, shared identity/access helpers, response model, tests for allowed and denied roles; regenerate the API reference.
* **Table** — Alembic migration + model + forced RLS + RLS test.
* **Event** — type, payload `TypedDict`, handler, client, docs, together.
* **Driver** — implement `SandboxDriverProtocol`, select it in `api/bootstrap/runtime.py`, add conformance tests.

---

## 6. Testing policy

* **Zero untested code.** A feature or fix is not done without tests that you have run. Unit tests in `tests/unit`, HTTP/DB integration in `tests/integration`, authorisation and isolation in `tests/security`, wire contracts in `tests/contract`, races in `tests/concurrency`, end-to-end agent behaviour in `tests/harness` and `apps/web/e2e`.
* Every backend route or feature has unit tests and an integration test; every UI change passes type-check, build and the Playwright suite, and its spec is updated in the same change. Run the **whole** Playwright suite, not only the spec you touched, and confirm zero regressions.
* Fix flakes at the cause; do not retry around them. A test that passes only in isolation is a finding.
* Slack behaviour is tested through the real Bolt app with recorded Web API calls (`tests/slack_harness.py`), not by calling internals.
* To prove a setting or agent field changes a whole turn, run one in memory with `tests/turn_harness.py` (`run_turn`: real orchestrator, turn engine, event bus and store; scripted model; fake sandbox) and assert on the model requests, sandbox commands and published events. Reading a key is not proof; a value that reaches a dataclass nobody uses is the usual way a setting ends up doing nothing.
* Verify against both mental models: the **Docker** driver (local) and the **Kubernetes** driver (cluster).

---

## 7. Documentation policy

Documentation is part of the change, not an afterthought, and it has exactly three homes:

1. **`doc/` — the user documentation site** (Install, Use, Administer, Operate, API). Written for users and operators only; it never links into `dev/`. Update it in the same change as any feature, protocol, authentication flow, API or UI change, keep `doc/docs.json` navigation current, and regenerate generated pages: `scripts/generate_api_reference.py` (REST reference + `openapi.json`), `scripts/generate_settings_reference.py`, and `pnpm run docs:screenshots` when the UI changed. `pnpm run docs:check`, `scripts/check_docs.py` and `tests/unit/test_docs_site.py` must pass.
2. **`dev/` — architecture for contributors:** `architecture.md` and `security-and-tenancy.md`. Update them when a structure, flow, invariant or extension point changes. Keep them descriptive and current; they are not a place for plans.
3. **This file** — the rules.

Also required: a `README.md` in every package and application (what it does, install, tests, environment variables); `CHANGELOG.md` entries under `## [Unreleased]` in `### Added | Changed | Fixed | Security` for every user-visible feature, fix or architectural change.

**Do not commit work-tracking files:** plans, roadmaps, status trackers, migration checklists, gap analyses, baselines, scratch notes. Track work in the issue tracker or the pull request; record outcomes in the code, the tests, the changelog and the two architecture documents. Release notes are generated from the changelog by `scripts/release.py notes` and are not committed.

---

## 8. Delivery

* **Packaging:** multi-stage `deploy/docker/Dockerfile.backend` and `Dockerfile.frontend` (plus the sandbox images); `deploy/docker-compose.yml` (evaluation), `docker-compose.prod.yml` (single-node production), `docker-compose.dev.yml` (development).
* **Helm (`deploy/helm/platform`):** any new environment variable, secret, mount, volume or route is added to `values.yaml`, `templates/` and `doc/` in the same change; run `helm lint`. Keep the cloud storage classes current (`gp3`, `managed-csi`, `pd-balanced`).
* **CI** (`.github/workflows/ci.yml`) runs independent jobs in parallel so a failure shows in about a minute: backend lint and types, unit tests with the golden set, database tests (PostgreSQL with forced row-level security), frontend lint/types/build, the Playwright suite in two shards against the in-memory stack and two against migrated PostgreSQL, docs, Helm and Compose validation, and (only when sandbox code or images changed on a pull request) the sandbox images and the Kubernetes driver on a real k3d cluster. The checks branch protection requires (`Backend Lint, Type-Check & Tests`, `Frontend Lint, Type-Check, Build & E2E`, `Documentation Validation`, `Helm & Docker Compose Validation`): keep those job names when changing the workflow; the first two are thin aggregators over the parallel jobs. Toolchain setup lives in `.github/actions/` (uv, pnpm and Playwright browsers are cached); database provisioning in `scripts/ci/`. Give every job a `timeout-minutes`.
* **Images** are built by one reusable workflow (`.github/workflows/image.yml`): each architecture natively on its own runner (no QEMU), pushed by digest, merged into one multi-arch tag, with its own cache scope. `release.yml` (on `vX.Y.Z` tags: verify, images, chart, SBOM, GitHub release) and `dev-images.yml` (manual, any branch) both call it.
* **Releases** use the one tool, `scripts/release.py` (`make release-prepare VERSION=X.Y.Z`, `make version-check`):
  ```bash
  python3 scripts/release.py prepare <version> [--skip-tests] [--create-pr]  # bump every manifest, rotate CHANGELOG, sync lockfiles, run gates
  python3 scripts/release.py check                                          # versions consistent everywhere
  python3 scripts/release.py notes <version> [--output <file>]              # notes for the GitHub release
  ```
  It covers `package.json`, `apps/web/package.json`, the Helm `Chart.yaml`/`values.yaml`, every `pyproject.toml`, `apps/api/src/api/main.py`, and the version references in the docs. Flow: branch `chore/release-vX.Y.Z` from `main` → `prepare` → PR → merge → tag `vX.Y.Z` → the release workflow publishes.
* **Operations** helpers: `scripts/rotate_master_key.py` (re-seal secrets after rotating `ENCRYPTION_MASTER_KEY`), `scripts/dr_drill.py` (backup/restore drill), `scripts/generate_sbom.py`.

---

## 9. Git hygiene

* Trunk-based: branch from `main`, small atomic commits, **Conventional Commits** (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`), a linear history (rebase or squash; no merge commits).
* Open a pull request against `main`; it merges only when every gate is green. Update `CHANGELOG.md` in the same pull request.
* Security issues are reported privately to `security@rocket-chat.dev`, not in public issues. Contributions are licensed GPL-3.0 (see `CONTRIBUTING.md`).

---

## 10. Agent operating protocol

On every task and change:

1. **Read before designing.** Start from this file, `dev/architecture.md`, `dev/security-and-tenancy.md` and the relevant `specifications/interfaces/*.py`; read the code you are about to change and its tests.
2. **Do not guess — ask.** If a requirement is ambiguous, a library behaves unexpectedly, or an architectural trade-off has to be made, stop and ask. Never make silent, uncoordinated architectural changes.
3. **Stay in scope.** Do the task that was asked; do not refactor, rename or "improve" unrelated code. Fix what you break.
4. **Test what you change, then say what you ran.** Report failures with their output; never claim a gate passed without running it, and say plainly what was skipped.
5. **Protect what is not yours.** Never point tests, migrations or scratch scripts at a database or cluster you did not create for the purpose; never switch the user's kube context; create throwaway infrastructure (own kubeconfig, spare ports) and delete it afterwards. Deploying, pushing images, publishing or anything else outward-facing happens only when explicitly asked, and the approval does not carry over to the next time.
6. **Keep the repository clean.** No work-tracking files, no debug leftovers, no commented-out code, no secrets. Commit only when asked, with a Conventional Commit message.
