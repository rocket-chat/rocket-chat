# Rocket Chat v0.1.4 (2026-10-07)

### Added

### Changed

### Fixed

### Security

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
