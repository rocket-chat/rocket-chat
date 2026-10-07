# Rocket Chat v0.1.4 (2026-10-07)

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

---

### Deployment Artifacts & Container Images

* **Backend Image (Multi-Arch `linux/amd64`, `linux/arm64`)**:
  ```bash
  docker pull ghcr.io/rocket-chat/backend:v0.1.4
  ```

* **Frontend Web Cockpit (`linux/amd64`, `linux/arm64`)**:
  ```bash
  docker pull ghcr.io/rocket-chat/frontend:v0.1.4
  ```

* **Kubernetes Helm Chart (OCI Registry)**:
  ```bash
  helm install rocket-chat oci://ghcr.io/rocket-chat/charts/rocket-chat --version 0.1.4
  ```
