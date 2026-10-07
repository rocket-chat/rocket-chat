# Rocket Chat v0.1.5 (2026-10-07)

### Added
- `auth`: Native Google Workspace NextAuth provider support with direct sign-in button on `/login`.
- `auth`: Organization-level email domain allowlist enforcement in NextAuth `signIn` callback, rejecting accounts with email domains outside authorized bounds.
- `settings`: UI-configurable SSO domain allowlist and enforcement toggle in Organization Settings (`/settings/org`).
- `helm`: Added `auth.google` configuration in Helm chart (`values.yaml`, `configmap.yaml`, `frontend/deployment.yaml`) supporting Google Client ID, Client Secret, and allowed domain hints.

### Changed

### Fixed

### Security

---

### Deployment Artifacts & Container Images

* **Backend Image (Multi-Arch `linux/amd64`, `linux/arm64`)**:
  ```bash
  docker pull ghcr.io/rocket-chat/backend:v0.1.5
  ```

* **Frontend Web Cockpit (`linux/amd64`, `linux/arm64`)**:
  ```bash
  docker pull ghcr.io/rocket-chat/frontend:v0.1.5
  ```

* **Kubernetes Helm Chart (OCI Registry)**:
  ```bash
  helm install rocket-chat oci://ghcr.io/rocket-chat/charts/rocket-chat --version 0.1.5
  ```
