# Rocket Chat v0.1.2 (2026-10-06)

### Added

### Changed

### Fixed
- `helm`: Release workflow no longer pushes Artifact Hub metadata to a stray `ghcr.io/rocket-chat/charts` package; it is pushed only to `charts/rocket-chat`.
- `helm`: Use the valid Artifact Hub category `ai-machine-learning` in `Chart.yaml` (`developer-tools` was rejected).

### Security

---

### Deployment Artifacts & Container Images

* **Backend Image (Multi-Arch `linux/amd64`, `linux/arm64`)**:
  ```bash
  docker pull ghcr.io/rocket-chat/backend:v0.1.2
  ```

* **Frontend Web Cockpit (`linux/amd64`, `linux/arm64`)**:
  ```bash
  docker pull ghcr.io/rocket-chat/frontend:v0.1.2
  ```

* **Kubernetes Helm Chart (OCI Registry)**:
  ```bash
  helm install rocket-chat oci://ghcr.io/rocket-chat/charts/rocket-chat --version 0.1.2
  ```
