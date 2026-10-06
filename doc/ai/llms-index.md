# Rocket Chat: LLM Knowledge Index

> Rocket Chat is an enterprise autonomous AI pair-programming platform combining an asynchronous ReAct state machine, deterministic Docker and Kubernetes execution sandboxes, kernel-enforced multi-tenancy via PostgreSQL Row-Level Security (RLS), an embedded Slack Socket Mode assistant, and a Next.js 15 Web Mission Control cockpit.

## Documentation Index

### Getting Started
- [Platform Overview](file:///Users/nperriolat/Dev/rocket-chat/doc/index.md): Mission, core value proposition, two-tier architecture diagram.
- [Quickstart Guide](file:///Users/nperriolat/Dev/rocket-chat/doc/getting-started/index.md): Single-command production stack and initial turn submission.
- [Local Developer Workflow](file:///Users/nperriolat/Dev/rocket-chat/doc/getting-started/local-development.md): Monorepo setup, uv, pnpm, local dev Postgres, testing gates.
- [Kubernetes Quickstart](file:///Users/nperriolat/Dev/rocket-chat/doc/getting-started/kubernetes-quickstart.md): Kind setup, Helm install, cloud CSIs (gp3, managed-csi, pd-balanced).
- [Slack App Setup](file:///Users/nperriolat/Dev/rocket-chat/doc/getting-started/slack-setup.md): Creating Slack App, Socket Mode, Bot Token scopes, interactive buttons.
- [GitHub App Setup](file:///Users/nperriolat/Dev/rocket-chat/doc/getting-started/github-app-setup.md): Registering App, webhook ingress, permissions, verified commit signing.
- [Master Configuration Reference](file:///Users/nperriolat/Dev/rocket-chat/doc/getting-started/configuration-reference.md): All environment variables and hierarchical policy schemas.

### Architecture & Subsystems
- [Architecture Overview](file:///Users/nperriolat/Dev/rocket-chat/doc/architecture/index.md): Two-tier system, request lifecycle sequence diagram.
- [Async ReAct Engine](file:///Users/nperriolat/Dev/rocket-chat/doc/architecture/agent-engine.md): ReAct state machine, CoT extraction, decision gates, 40k token compaction.
- [Dual Sandbox Engine](file:///Users/nperriolat/Dev/rocket-chat/doc/architecture/sandbox-runtimes.md): Docker and K8s drivers, scale-to-zero, soft node-affinity pinning (`< 1.5s` resume).
- [Custom Sandboxes & Warm Pools](file:///Users/nperriolat/Dev/rocket-chat/doc/architecture/custom-sandboxes.md): Custom container images, pre-warmed pool pods (`< 200ms` startup), inactivity pausing and cleanup.
- [LiteLLM Gateway](file:///Users/nperriolat/Dev/rocket-chat/doc/architecture/llm-gateway.md): Universal routing, hardware AES-256-GCM cipher with PBKDF2 (100k rounds).
- [Multi-Tenancy & PostgreSQL RLS](file:///Users/nperriolat/Dev/rocket-chat/doc/architecture/multi-tenancy.md): Row-Level Security, rocket_app non-superuser role, OIDC RS256 auth middleware.
- [Hierarchical Configuration](file:///Users/nperriolat/Dev/rocket-chat/doc/architecture/configuration-engine.md): 4-tier cascade, PolicyViolationError enforcement.
- [Git Engine](file:///Users/nperriolat/Dev/rocket-chat/doc/architecture/git-engine.md): Co-authored-by trailers, GPG / GitHub App verified commit signing, webhook resume.
- [Slack Assistant](file:///Users/nperriolat/Dev/rocket-chat/doc/architecture/slack-assistant.md): 100% outbound Socket Mode, zero ingress, thread spinners, Block Kit decision gates.
- [Web Mission Control](file:///Users/nperriolat/Dev/rocket-chat/doc/architecture/mission-control-ui.md): Cosmic Telemetry design system, Monaco DiffEditor, xterm logs.
- [Extensibility & Custom Tools](file:///Users/nperriolat/Dev/rocket-chat/doc/architecture/extensibility-and-tools.md): Writing custom tools, FastMCP integration, custom sandbox drivers.

### API & Contracts
- [API Overview](file:///Users/nperriolat/Dev/rocket-chat/doc/api-reference/index.md): Interface matrix and interactive Swagger/ReDoc links.
- [REST API Reference](file:///Users/nperriolat/Dev/rocket-chat/doc/api-reference/rest-endpoints.md): Endpoints, schemas, request/response bodies.
- [WebSocket Protocol](file:///Users/nperriolat/Dev/rocket-chat/doc/api-reference/websocket-protocol.md): Real-time telemetry events and client messages.
- [Python Protocol Interfaces](file:///Users/nperriolat/Dev/rocket-chat/doc/api-reference/python-protocols.md): Core typing.Protocol contracts in specifications.interfaces.*.

### Deployment & Operations
- [Operations Overview](file:///Users/nperriolat/Dev/rocket-chat/doc/deployment-and-ops/index.md): Deployment model matrix and release artifacts.
- [Production Docker Compose](file:///Users/nperriolat/Dev/rocket-chat/doc/deployment-and-ops/docker-compose.md): 3-container topology, init-rls.sql, entrypoint migrations.
- [Production Helm Chart](file:///Users/nperriolat/Dev/rocket-chat/doc/deployment-and-ops/helm-chart.md): Manifests, agent-sandboxes namespace, RBAC, values.
- [Cloud CSIs & Storage Classes](file:///Users/nperriolat/Dev/rocket-chat/doc/deployment-and-ops/cloud-storage-csi.md): AWS gp3, Azure managed-csi, GCP pd-balanced, soft affinity resume.
- [Security & Compliance](file:///Users/nperriolat/Dev/rocket-chat/doc/deployment-and-ops/security-and-compliance.md): SSRF mitigation (169.254.169.254/32), defense-in-depth layers.
- [CI/CD Pipelines](file:///Users/nperriolat/Dev/rocket-chat/doc/deployment-and-ops/ci-cd-pipelines.md): GitHub Actions ci.yml and release.yml multi-arch pipelines.
- [Production Hardening & Disaster Recovery](file:///Users/nperriolat/Dev/rocket-chat/doc/deployment-and-ops/production-hardening.md): External RDS/Cloud SQL, HPA autoscaling, backups, master key custody.
- [Troubleshooting & Operations Runbook](file:///Users/nperriolat/Dev/rocket-chat/doc/deployment-and-ops/troubleshooting.md): Diagnosis and fix for pending pods, RLS errors, Slack/GitHub issues.
