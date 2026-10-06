# Rocket Chat v0.1.1 (2026-10-06)

### Added
- `helm`: Added Artifact Hub repository verification metadata (`artifacthub-repo.yml`) with verified publisher ID `26c5b29e-9eec-4118-bf94-0ec72d17470e`.
- `helm`: Added Artifact Hub badge, comprehensive chart description, metadata annotations, and ORAS OCI metadata layer push to release workflow.
- `web`: Added Artifact Hub badge and Helm deployment guide to root README.

### Fixed
- `docker`: Fixed frontend Dockerfile multi-arch container build failure by pinning builder to `pnpm@10` to avoid `ERR_PNPM_IGNORED_BUILDS` in pnpm 11.

---

### Deployment Artifacts & Container Images

* **Backend Image (Multi-Arch `linux/amd64`, `linux/arm64`)**:
  ```bash
  docker pull ghcr.io/rocket-chat/backend:v0.1.1
  ```

* **Frontend Web Cockpit (`linux/amd64`, `linux/arm64`)**:
  ```bash
  docker pull ghcr.io/rocket-chat/frontend:v0.1.1
  ```

* **Kubernetes Helm Chart (OCI Registry)**:
  ```bash
  helm install rocket-chat oci://ghcr.io/rocket-chat/charts/rocket-chat --version 0.1.1
  ```
