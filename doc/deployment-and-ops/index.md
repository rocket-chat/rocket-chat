# Operations & Delivery Overview

Rocket Chat is packaged for enterprise production deployment across both lightweight container environments (Docker Compose) and cloud Kubernetes clusters (Helm).

---

## 1. Deployment Model Matrix

| Environment | Packaging Method | Target Use Case | Orchestrator | Storage Mechanism |
| :--- | :--- | :--- | :--- | :--- |
| **Local / Single Node** | `deploy/docker-compose.prod.yml` | Small teams, staging, developer workstations | Docker Engine / Compose | Host Docker named volumes |
| **Enterprise Cloud K8s** | `deploy/helm/platform/` | Multi-tenant production, autoscaling clusters | Kubernetes 1.28+ | Cloud CSIs (`gp3`, `managed-csi`, `pd-balanced`) |

---

## 2. Release & Packaging Artifacts

1. **Backend Container:** `ghcr.io/rocket-chat/backend:latest` (Multi-arch `linux/amd64`, `linux/arm64`)
2. **Frontend Container:** `ghcr.io/rocket-chat/frontend:latest` (Multi-arch `linux/amd64`, `linux/arm64`)
3. **Helm OCI Chart:** `oci://ghcr.io/rocket-chat/charts/rocket-chat:0.1.3`
