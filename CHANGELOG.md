# Changelog

All notable changes to **Rocket Chat** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- `helm`: Added `auth.nextauthUrl` value to Helm chart, automatically resolving `https://` scheme when `ingress.tls` is configured and allowing custom canonical NextAuth URL overrides.
- `helm`: Enhanced `NEXTAUTH_SECRET` handling to prioritize `auth.existingSecret` over default values, allowing seamless integration with External Secrets Operator and HashiCorp Vault.

### Changed

### Fixed
- `helm`: Fixed PostgreSQL startup failure on persistent volume mounts containing `lost+found` by configuring `PGDATA=/var/lib/postgresql/data/pgdata` with volume `subPath: pgdata`.

### Security

## [0.1.5] - 2026-10-07

### Added
- `auth`: Native Google Workspace NextAuth provider support with direct sign-in button on `/login`.
- `auth`: Organization-level email domain allowlist enforcement in NextAuth `signIn` callback, rejecting accounts with email domains outside authorized bounds.
- `settings`: UI-configurable SSO domain allowlist and enforcement toggle in Organization Settings (`/settings/org`).
- `helm`: Added `auth.google` configuration in Helm chart (`values.yaml`, `configmap.yaml`, `frontend/deployment.yaml`) supporting Google Client ID, Client Secret, and allowed domain hints.

### Changed

### Fixed

### Security

## [0.1.4] - 2026-10-07

### Added
- `telemetry`: Real-time token and dollar cost usage ledger integration. Orchestrator streaming cycles (`_stream_model_step`) and subagent loops (`SubagentRunner`) now actively record prompt and completion tokens into `LiteLLMGateway` (resolves #4).
- `auth`: NextAuth enterprise SSO integration supporting GitHub, Google, OIDC, and Credentials with customized cockpit login portal (`/login`).
- `auth`: WebSocket authentication with JWT validation and strict session ownership enforcement on `/sessions/{session_id}/ws`.
- `git`: On-demand dynamic GitHub App installation tokens when user OAuth is absent, with configurable repository permissions (`contents`, `pull_requests`, `issues`) in Organization Settings.
- `helm`: Dedicated PostgreSQL `StatefulSet` with `volumeClaimTemplate` (`10Gi` persistent volume) replacing the previous ephemeral `emptyDir` deployment.
- `helm`: Direct Ingress routing for `/api` and `/v1` to the backend control plane service.

### Changed
- `theme`: Fixed light theme text contrast across Cockpit and Settings, ensuring readable typography on light backgrounds.
- `telemetry`: Removed hardcoded mock fallbacks in `/v1/settings/org/usage` and `/v1/settings/user/usage`, now returning live ledger counts.
- `settings`: Removed static demo placeholder identities and sample team roster from Settings UI.

### Fixed
- `usage`: Fixed issue where organization usage stayed at zero and personal user token usage was not incremented across agent sessions (#4).

## [0.1.3] - 2026-10-07

### Added
- `helm`: Native support for referencing external Kubernetes secrets via `existingSecret` and `existingSecretKey` for database connection (`DATABASE_URL`, `ADMIN_DATABASE_URL`), encryption master key (`ENCRYPTION_MASTER_KEY` / `ROCKET_ENCRYPTION_KEY`), model provider keys (`models.<provider>.existingSecret`), GitHub App credentials, Slack bot/app tokens, and PostgreSQL subchart password.
- `helm`: Support for injecting custom extra secrets (`backend.extraSecretRefs`) and arbitrary environment variables (`backend.extraEnv`) in the backend deployment.
- `config`: Support for reading `ROCKET_ENCRYPTION_KEY` alongside `ENCRYPTION_MASTER_KEY` in the encryption engine.

### Changed
- `models`: Default system model updated to `openrouter/deepseek/deepseek-v4.1-flash` across backend configuration engines, API fallbacks, documentation, and frontend model settings.
- `models`: Token context limits configured for `deepseek-v4.1` and `deepseek-v4.1-flash` (131,072 context tokens).

### Security
- `deps`: Patched PostCSS (`>=8.5.23`) and DOMPurify (`>=3.4.16`) dependency vulnerabilities via pnpm overrides.
- `helm`: External secret support eliminates requirement for hardcoded plaintext credentials in Helm `values.yaml` files.

## [0.1.2] - 2026-10-06

### Added

### Changed

### Fixed
- `helm`: Release workflow no longer pushes Artifact Hub metadata to a stray `ghcr.io/rocket-chat/charts` package; it is pushed only to `charts/rocket-chat`.
- `helm`: Use the valid Artifact Hub category `ai-machine-learning` in `Chart.yaml` (`developer-tools` was rejected).

### Security

## [0.1.1] - 2026-10-06

### Added
- `helm`: Added Artifact Hub repository verification metadata (`artifacthub-repo.yml`) with verified publisher ID `26c5b29e-9eec-4118-bf94-0ec72d17470e`.
- `helm`: Added Artifact Hub badge, comprehensive chart description, metadata annotations, and ORAS OCI metadata layer push to release workflow.
- `web`: Added Artifact Hub badge and Helm deployment guide to root README.

### Fixed
- `docker`: Fixed frontend Dockerfile multi-arch container build failure by pinning builder to `pnpm@10` to avoid `ERR_PNPM_IGNORED_BUILDS` in pnpm 11.

## [0.1.0] - 2026-10-06

### Added
  - 3-tier context hygiene with bounded head/tail clamping (`clamp_output`) and automatic 70% threshold receipt compaction.
  - Concurrency safety with per-file `asyncio.Lock` guards and `contextvars`-scoped execution isolation.
  - Frontier reasoning support for thinking budgets (`thinking_budget_tokens`) and reasoning effort (`reasoning_effort`).
  - Specialized autonomous subagent delegation framework with bounded turn limits and isolated tool manifests.
  - Multi-tenant sandbox fleet manager supporting customizable Docker and Kubernetes runtime container images.
  - Organization-wide and per-agent sandbox configuration with custom CPU, memory limits, and default Python runtime image.
  - Full-duplex interactive streaming terminal with PTY bridge over WebSockets.
  - Automatic pod lifecycle with volume detachment mitigations and hibernating state persistence.
  - FastAPI control plane with real-time Server-Sent Events (SSE) and bidirectional WebSockets.
  - Organization and user token usage accounting with cost and quota tracking.
  - Dual-layer cascading settings engine (`system -> org -> agent -> session`).
  - Cold process restart state preservation with PostgreSQL and tenant Row-Level Security.
  - Next.js 15 pair programming cockpit featuring calm obsidian dark/light themes.
  - Continuous flight log stream with collapsed-by-default reasoning accordions.
  - Monaco-powered surgical diff viewer and predictive `[Tab ⇥]` follow-up autocompletion.
  - Full-page enterprise governance suite for Agents, Subagents, MCP Servers, Skills, Sandboxes, Usage, and GitHub.
  - Multi-scheme Model Context Protocol (MCP) registry supporting `stdio`, `http`, and `sse` transports with write-only encrypted credentials.
  - Dynamic OpenRouter LLM discovery with hundreds of live provider models.
  - Isolated Git working tree engine with branch management and commit signing.
  - Automated GitHub Pull Request generation with customizable templates and co-authorship attribution trailers.
  - Production multi-stage Dockerfiles (`Dockerfile.backend`, `Dockerfile.frontend`) with multi-arch packaging (`linux/amd64`, `linux/arm64`).
  - Official production Helm chart with OCI registry distribution (`oci://ghcr.io/rocket-chat/charts/rocket-chat`).
  - Automated local development cluster Makefile orchestration with Kind and K3d.
  - Central Mintlify documentation site with full architecture blueprints, API references, and cloud CSI deployment guides.

### Changed

### Fixed

### Security
