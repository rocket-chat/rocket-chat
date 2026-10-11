# Rocket Chat Deployment & Packaging

This directory contains the production and local deployment assets for the **Rocket Chat** platform.

## Directory Structure

```text
deploy/
├── docker/
│   ├── Dockerfile.backend        # Multi-stage uv-based Python 3.12+ backend image
│   ├── Dockerfile.frontend       # Multi-stage standalone Next.js 15 frontend image
│   ├── entrypoint.backend.sh     # Database migration and service startup script
│   └── init-rls.sql              # PostgreSQL init script for Row-Level Security
├── docker-compose.prod.yml       # Production Compose stack (required secrets, ENVIRONMENT=production)
├── docker-compose.yml            # Evaluation stack (throwaway secrets, developer login)
├── .env.example                  # Required settings for the production stack
├── docker-compose.dev.yml        # Development mode stack (Postgres + pgvector)
└── helm/
    └── platform/                 # Official production Helm chart
```

---

## 1. Docker Compose

**Try it locally** (throwaway credentials, one-click developer login - never expose this):

```bash
docker compose -f deploy/docker-compose.yml up -d
```

**Run it for real** on a single node. The production stack refuses to start without its secrets and
runs the API in `ENVIRONMENT=production` (no developer login, no trusted proxy headers):

```bash
cp deploy/.env.example deploy/.env      # fill in every REQUIRED value and one sign-in method
docker compose --env-file deploy/.env -f deploy/docker-compose.prod.yml up -d
```

### Health Check Endpoints:
- **Backend API:** `curl -f http://localhost:8000/health`
- **Web Frontend:** `curl -f http://localhost:3000/`

To shut down:
```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.prod.yml down
```

---

## 2. Cloud Kubernetes Deployment (Helm)

Install the chart directly into your Kubernetes cluster:

```bash
# Lint the chart
helm lint deploy/helm/platform

# Dry-run template rendering
helm template test-release deploy/helm/platform

# Install onto cluster
helm install rocket-chat deploy/helm/platform \
  --namespace rocket-chat \
  --create-namespace \
  --set sandbox.storageClass="gp3"
```
