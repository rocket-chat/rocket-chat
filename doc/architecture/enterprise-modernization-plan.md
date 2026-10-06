# Step-by-Step Implementation Plan: Complete Enterprise Modernization

This tracking document outlines the phased implementation for all platform improvements, addressing the review findings and user requirements:

- **Track 1**: Real-Time Bidirectional Terminal PTY & Streaming Telemetry
- **Track 2**: Token Budget Accounting, Persistent Usage Ledger & Telemetry Panels
- **Track 3**: Full-Lifecycle GitHub Integration, Authenticated Git Proxy & Live PRs
- **Track 4**: Org Sandbox Fleet Manager & Dynamic Agent Persona Tuning (Sandbox Image, Resources, Model Hyperparameters)
- **Track 5**: Monolithic Route Deconstruction & Turn History State Synchronization
- **Track 6**: Comprehensive Verification, Documentation & CI/CD Validation

---

## Progress Checklist

- [ ] **Track 1: Terminal PTY Streaming & FastMCP Telemetry**
  - [ ] 1.1 Implement `DockerSandboxDriver.attach_pty` bidirectional byte streaming with tty.
  - [ ] 1.2 Implement `K8sSandboxDriver.attach_pty` via namespaced pod exec.
  - [ ] 1.3 Update WebSocket endpoint in `routes.py` to handle `terminal_input` events and stream PTY output.
  - [ ] 1.4 Update `StreamingTerminal.tsx` with bidirectional keyboard input (`disableStdin: false`, `onData`).
  - [ ] 1.5 Add FastMCP progress streaming events during tool calls.
  - [ ] 1.6 Unit test terminal PTY streaming and input routing.

- [ ] **Track 2: Token Budget Accounting & Usage Telemetry**
  - [ ] 2.1 Implement `BudgetLedger` in `llm-gateway` and database schema.
  - [ ] 2.2 Wire `LiteLLMGateway.check_budget` and `record_usage` with real dollar/token calculation.
  - [ ] 2.3 Implement REST endpoints `GET /v1/settings/org/usage` and `GET /v1/settings/user/usage`.
  - [ ] 2.4 Create Org Usage Telemetry UI (`apps/web/src/app/settings/org/usage/page.tsx`).
  - [ ] 2.5 Create User Usage Telemetry UI (`apps/web/src/app/settings/user/usage/page.tsx`).
  - [ ] 2.6 Update `SettingsNav.tsx` with navigation badges for usage.
  - [ ] 2.7 Unit and integration tests for budget enforcement.

- [ ] **Track 3: GitHub Integration, Git Proxy & Real PR Creation**
  - [ ] 3.1 Upgrade `GitEngine.push_and_open_pr` to create real GitHub PRs via REST API.
  - [ ] 3.2 Secure Git proxy token injection for remote authentication without secret leakage.
  - [ ] 3.3 Add automated commit trailer formatting with GPG signing fallback.
  - [ ] 3.4 Unit tests for GitHub PR creation and webhook HMAC verification.

- [ ] **Track 4: Org Sandbox Fleet Manager & Agent Persona Customization**
  - [ ] 4.1 Add `sandboxes` domain in `SYSTEM_DOMAIN_DEFAULTS` with configurable images and resource limits.
  - [ ] 4.2 Create Org Sandbox Fleet Management page (`apps/web/src/app/settings/org/sandboxes/page.tsx`).
  - [ ] 4.3 Expand `AgentPersonaModel` with `sandbox_image`, `resources`, `temperature`, `thinking_budget_tokens`, `reasoning_effort`.
  - [ ] 4.4 Update Custom Agents UI (`apps/web/src/app/settings/agents/page.tsx`) to edit sandbox and model parameters.
  - [ ] 4.5 Bind custom agent sandbox image and model parameters into `WorkspaceSpec` and `ModelRequest`.
  - [ ] 4.6 Unit and integration tests for persona parameter cascading.

- [ ] **Track 5: Monolithic Route Deconstruction & Turn History Synchronization**
  - [ ] 5.1 Extract `TurnExecutionService`, `WorkspaceProvisioner`, and `TurnHistorySynchronizer`.
  - [ ] 5.2 Atomic synchronization of intermediate tool calls, tool responses, thoughts, and assistant replies.
  - [ ] 5.3 Persist cascading settings in database store instead of in-memory dictionaries.
  - [ ] 5.4 Clean up silent exception swallowing and duplicate base64 helper scripts.

- [ ] **Track 6: Verification, Documentation & CI/CD**
  - [ ] 6.1 Run full Python test suite (`pytest tests/`).
  - [ ] 6.2 Run linting and formatting (`ruff check`, `ruff format`, `mypy`).
  - [ ] 6.3 Run frontend type checks and lints (`pnpm --filter web type-check`, `lint`).
  - [ ] 6.4 Update documentation (`doc/architecture/` and Mintlify navigation).
  - [ ] 6.5 Run documentation validation (`pnpm run docs:check`).
