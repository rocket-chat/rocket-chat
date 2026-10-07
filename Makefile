.PHONY: dev-cluster cluster-up images-build images-load helm-install helm-uninstall cluster-down docs-dev docs-check help release-prepare release-notes version-check

KIND_CLUSTER_NAME ?= rocket-local

help:
	@echo "Rocket Chat Kubernetes Orchestration & Developer Tooling Targets:"
	@echo "  make dev-cluster     - Full setup: creates Kind cluster, builds & loads images, deploys Helm chart"
	@echo "  make cluster-up      - Creates local Kind cluster with host port mappings (8000, 3000)"
	@echo "  make images-build    - Builds backend and frontend production container images"
	@echo "  make images-load     - Imports images into the Kind containerd runtime"
	@echo "  make helm-install    - Installs/upgrades Helm chart using values.local.yaml and .env API keys"
	@echo "  make helm-uninstall  - Removes the Helm release"
	@echo "  make cluster-down    - Tears down the local Kind cluster"
	@echo "  make docs-dev        - Starts live Mintlify documentation preview server on port 3333"
	@echo "  make docs-check      - Validates Mintlify documentation configuration and links"
	@echo "  make version-check   - Verifies version synchronization across all repository manifests"
	@echo "  make release-prepare - Prepares CHANGELOG, bumps versions, and generates release notes (e.g. make release-prepare VERSION=0.2.0)"
	@echo "  make release-notes   - Extracts release notes for a version (e.g. make release-notes VERSION=X.Y.Z)"

version-check:
	python3 scripts/release.py check

release-prepare:
	@if [ -z "$(VERSION)" ]; then echo "Error: VERSION is required. Example: make release-prepare VERSION=X.Y.Z" && exit 1; fi
	python3 scripts/release.py prepare $(VERSION)

release-notes:
	@if [ -z "$(VERSION)" ]; then echo "Error: VERSION is required. Example: make release-notes VERSION=X.Y.Z" && exit 1; fi
	python3 scripts/release.py notes $(VERSION)

docs-dev:
	pnpm run docs:dev

docs-check:
	pnpm run docs:check

dev-cluster: cluster-up images-build images-load helm-install

cluster-up:
	@if ! kind get clusters 2>/dev/null | grep -q "^$(KIND_CLUSTER_NAME)$$"; then \
		echo "Creating Kind cluster $(KIND_CLUSTER_NAME)..."; \
		kind create cluster --name $(KIND_CLUSTER_NAME) --config deploy/kind-cluster.yaml; \
	else \
		echo "Kind cluster $(KIND_CLUSTER_NAME) is already running."; \
	fi

images-build:
	@echo "Building backend Docker image..."
	docker build -t ghcr.io/rocket-chat/backend:latest -f deploy/docker/Dockerfile.backend .
	@echo "Building frontend Docker image..."
	docker build -t ghcr.io/rocket-chat/frontend:latest -f deploy/docker/Dockerfile.frontend .

images-load:
	@echo "Loading images into Kind cluster..."
	kind load docker-image --name $(KIND_CLUSTER_NAME) ghcr.io/rocket-chat/backend:latest
	kind load docker-image --name $(KIND_CLUSTER_NAME) ghcr.io/rocket-chat/frontend:latest
	docker exec $(KIND_CLUSTER_NAME)-control-plane crictl pull pgvector/pgvector:pg16
	docker exec $(KIND_CLUSTER_NAME)-control-plane crictl pull python:3.12-slim

helm-install:
	@OPENROUTER_KEY=$$(python3 -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.getenv('OPENROUTER_API_KEY', ''))"); \
	DEFAULT_MODEL=$$(python3 -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.getenv('ROCKET_DEFAULT_MODEL', 'openrouter/deepseek/deepseek-v4.1-flash'))"); \
	echo "Deploying Rocket Chat platform via Helm..."; \
	helm upgrade --install rocket-chat deploy/helm/platform \
		--namespace rocket-chat \
		--create-namespace \
		-f deploy/helm/platform/values.local.yaml \
		--set models.openrouter.apiKey="$$OPENROUTER_KEY" \
		--set backend.env.ROCKET_DEFAULT_MODEL="$$DEFAULT_MODEL"

helm-uninstall:
	@echo "Uninstalling Rocket Chat Helm release..."
	helm uninstall rocket-chat --namespace rocket-chat

cluster-down:
	@echo "Deleting Kind cluster $(KIND_CLUSTER_NAME)..."; \
	kind delete cluster --name $(KIND_CLUSTER_NAME)
