# Source Code & Local Monorepo Setup

This guide walks you through cloning the **Rocket Chat** monorepo and running it locally from source for development, contributing, or testing custom components.

> **Tip:** If you only want to run or deploy Rocket Chat without modifying the code, see the [Getting Started Guide](/getting-started/index) which uses published pre-built images, or deploy via our official Helm chart (`oci://ghcr.io/rocket-chat/charts/rocket-chat`).

---

## 1. Prerequisites

Before cloning the repository, ensure you have:
- **Git**
- **Docker Engine** (or Docker Desktop) 24.0+
- **Python** 3.12+ and **[uv](https://docs.astral.sh/uv/)**
- **Node.js** 22+ and **[pnpm](https://pnpm.io/)** v10+

---

## 2. Cloning the Repository

Clone the repository and enter the project directory:

```bash
git clone https://github.com/rocket-chat/rocket-chat.git
cd rocket-chat
```

---

## 3. Running via Local Production Docker Compose

To run the complete platform locally using the repository's production Docker Compose stack:

```bash
docker compose -f deploy/docker-compose.prod.yml up -d
```

Verify that all three services are up and healthy:
```bash
docker compose -f deploy/docker-compose.prod.yml ps
```

Once running:
- **Web Cockpit:** [http://localhost:3000](http://localhost:3000)
- **API Control Plane:** [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 4. Running for Local Development (Live Reload)

If you are developing features or modifying backend/frontend code:

### Backend Control Plane
```bash
# Sync dependencies
uv sync --dev

# Run FastAPI development server with hot-reloading
uv run uvicorn api.main:app --app-dir apps/api/src --reload --port 8000
```

### Frontend Mission Control
```bash
# Install Node dependencies
pnpm install

# Start Next.js 15 dev server
pnpm dev
```

---

## 5. Local Kubernetes Cluster (Kind + Helm)

To test the Kubernetes sandbox driver against local source builds:

```bash
# Automatically creates Kind cluster, builds local images, and deploys Helm chart
make dev-cluster

# Tear down cluster when finished
make cluster-down
```
