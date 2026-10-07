# Rocket Chat v0.1.3 (2026-10-07)

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

---

### Deployment Artifacts & Container Images

* **Backend Image (Multi-Arch `linux/amd64`, `linux/arm64`)**:
  ```bash
  docker pull ghcr.io/rocket-chat/backend:v0.1.3
  ```

* **Frontend Web Cockpit (`linux/amd64`, `linux/arm64`)**:
  ```bash
  docker pull ghcr.io/rocket-chat/frontend:v0.1.3
  ```

* **Kubernetes Helm Chart (OCI Registry)**:
  ```bash
  helm install rocket-chat oci://ghcr.io/rocket-chat/charts/rocket-chat --version 0.1.3
  ```
