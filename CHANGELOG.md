# Changelog

All notable changes to **Rocket Chat** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- `orchestrator`:
- `sandboxes`:
- `api`:
- `web`:
- `settings`:
- `git`:
- `helm`:

### Changed

### Fixed

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
