# Production Hardening & Disaster Recovery

This guide covers operational best practices for deploying Rocket Chat in mission-critical enterprise environments.

---

## 1. Managed PostgreSQL Integration

In enterprise production, do not run stateful PostgreSQL containers inside your Kubernetes cluster. Instead, connect to an external managed database with automated failover and multi-AZ replication:

- **AWS:** Amazon Aurora PostgreSQL (or RDS PostgreSQL 16) with `pgvector` extension enabled.
- **Azure:** Azure Database for PostgreSQL Flexible Server (v16).
- **Google Cloud:** Cloud SQL for PostgreSQL (v16).

### Provisioning Application Roles for Row-Level Security
Run this one-time provisioning script as the database superuser (`postgres` / `cloudsqladmin` / `rds_superuser`):

```sql
-- 1. Enable vector extension for documentation/codebase RAG
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Create the non-superuser application role
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rocket_app') THEN
        CREATE ROLE rocket_app WITH LOGIN PASSWORD 'YOUR_STRONG_PROD_PASSWORD';
    END IF;
END
$$;

-- 3. Grant schema permissions
GRANT ALL PRIVILEGES ON DATABASE rocket_chat TO rocket_app;
GRANT ALL PRIVILEGES ON SCHEMA public TO rocket_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO rocket_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO rocket_app;
```

---

## 2. Running Alembic Migrations in Kubernetes

To run database migrations safely before rolling out new backend pods, use a **Kubernetes Job** as a Helm `pre-install,pre-upgrade` hook:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: rocket-chat-db-migration
  annotations:
    "helm.sh/hook": pre-install,pre-upgrade
    "helm.sh/hook-weight": "1"
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
spec:
  template:
    spec:
      restartPolicy: OnFailure
      containers:
        - name: alembic-migration
          image: "ghcr.io/rocket-chat/backend:latest"
          command: ["alembic", "upgrade", "head"]
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: rocket-chat-secrets
                  key: ADMIN_DATABASE_URL
```

---

## 3. High Availability & Horizontal Pod Autoscaling (HPA)

Both the **Backend Control Plane** and **Frontend Web** tiers are stateless and scale horizontally behind standard Kubernetes Services.

### Horizontal Pod Autoscaler (HPA) Manifest
```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: rocket-chat-backend-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: rocket-chat-backend
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

---

## 4. Disaster Recovery & Backup Strategy

### A. Database Backups (`pg_dump`)
Automate daily logical backups:
```bash
pg_dump -h <db-host> -U postgres -d rocket_chat -Fc -f /backups/rocket_chat_$(date +%F).dump
```

To restore:
```bash
pg_restore -h <db-host> -U postgres -d rocket_chat --clean /backups/rocket_chat_2026-10-03.dump
```

### B. Master Encryption Key Custody
The `ENCRYPTION_MASTER_KEY` encrypts all tenant BYOK credentials at rest.
> [!CAUTION]
> If `ENCRYPTION_MASTER_KEY` is lost, all encrypted BYOK credentials stored in the database are permanently unrecoverable. Store this secret in an enterprise secret vault (AWS Secrets Manager, HashiCorp Vault, Azure Key Vault).

---

## 5. Health Probes, Draining & Capacity

Both the backend and frontend deployments expose a **startup, liveness and readiness** probe, wired through `backend.probes.*` / `frontend.probes.*` in `values.yaml`:

| Probe | Backend path | Purpose |
| :--- | :--- | :--- |
| Startup | `/health` | Absorbs first-boot latency (30 × 5 s) without tripping liveness |
| Liveness | `/health` | **Static** signal — a hung process restarts, a database outage does **not** |
| Readiness | `/v1/ready` | Pings PostgreSQL with a hard timeout and reports code-index stats; returns **503** when the database is unreachable so traffic drains away |

The backend `resources` are sized against the M2 code-index math: each full session is ~9 MB (1500 chunks × 1536 float32), the process-wide code-index ceiling is 192 MiB (`ROCKET_CODE_INDEX_MAX_BYTES`), and tokenizer/context buffers add headroom — hence `requests.memory: 1Gi` and `limits.memory: 2Gi`. Raise the ceiling only after raising the limit.

### Graceful drain

Rolling updates use `maxUnavailable: 0` / `maxSurge: 1`, and each backend pod has `terminationGracePeriodSeconds: 60` plus a `preStop` hook:

```yaml
lifecycle:
  preStop:
    exec:
      command: ["/bin/sh", "-c", "curl -fsS -X POST http://127.0.0.1:8000/v1/drain || true; sleep 10"]
```

`POST /v1/drain` flips the pod into draining mode, which makes `/v1/ready` fail so Kubernetes removes it from Service endpoints **before** SIGTERM; in-flight turns then finish within the grace period.

A turn can run for hours, far longer than any grace period. By default a turn still running when the pod stops is interrupted and resumed on another pod with a recap of what it had done. To let turns finish instead, set `backend.env.ROCKET_DRAIN_WAIT_SECONDS` (shutdown waits that long for the turns this replica runs, then stops) and raise `backend.terminationGracePeriodSeconds` above it, `preStop` included. Use it with several replicas or the cluster bus: a single replica's successor marks every turn it finds running at start-up as interrupted, which would take the turn from the pod that is still finishing it.

### Running more than one replica

With `backend.eventBus: auto` (the default) the chart switches the backend to the **PostgreSQL event bus** as soon as more than one replica can run (`replicas > 1` or autoscaling enabled):

- Session events, their sequence numbers, approval / question answers and cancels are shared through `LISTEN`/`NOTIFY`, so a WebSocket on one pod follows a turn running on another. A conversation opened on a pod that is not running its turn asks the pod that is for what it has streamed since its last stored step, so no sticky sessions are needed at the ingress. Each session's status changes also go to its organization's topic for the sidebar socket (`/v1/sidebar/ws`).
- A session runs **one turn at a time cluster-wide** (a PostgreSQL advisory lock held for the life of the turn; it is released with the connection if the pod dies). A turn whose pod dies is marked `interrupted` once its lease goes stale, and one replica **resumes it automatically**: a fresh turn tells the agent the server restarted and it continues from the saved conversation and the (surviving) sandbox. This happens at most `ROCKET_TURN_AUTO_RESUME_ATTEMPTS` times per task, only within 15 minutes of the interruption, never when the user has written since, and never when the organization sets `governance.auto_resume_turns` to off; otherwise the user can still retry from the inspector.
- **Slack Socket Mode runs on one elected replica.** Slack delivers every event to every open connection, so the replicas elect a leader through a PostgreSQL advisory lock; the others stay on standby (they still serve pairing and workspace binding) and one takes over within seconds if the leader stops.
- Still per pod: the code-index cache (derived, rebuilt on demand) and the API rate-limit counters (effective ceiling up to N times the configured value). `ClientIP` affinity on the backend Service remains as a safety net, not a requirement.

## 6. Observability: `/metrics`, Alerts & Dashboard

The backend exposes Prometheus text metrics at `GET /metrics` (no scrape sidecar required). The documented series:

| Metric | Type | Meaning |
| :--- | :--- | :--- |
| `rocket_turns_total{kind}` | counter | Completed turns (conversational / sandbox) |
| `rocket_turn_duration_seconds{kind}` | histogram | End-to-end turn duration |
| `rocket_turn_time_to_first_token_seconds{kind}` | histogram | TTFT (target: under 2 s conversational / under 6 s sandbox, p95) |
| `rocket_tool_calls_total{tool}` | counter | Tool invocations |
| `rocket_tool_duration_seconds{tool}` | histogram | Tool execution duration |
| `rocket_model_tokens_total{model,type}` | counter | Prompt/completion/cache tokens from the ledger |
| `rocket_model_cost_usd_total{model}` | counter | Estimated model spend |
| `rocket_prompt_cache_hit_ratio` | gauge | Cached prompt tokens ÷ prompt tokens (target: ≥60% on long sessions) |
| `rocket_websocket_connections` | gauge | Open cockpit WebSockets |
| `rocket_code_index_{sessions,bytes}` | gauge | Code-index cache footprint |
| `rocket_code_index_{hits,misses,evictions,refusals}_total` | counter | Cache events |
| `rocket_quota_rejections_total{scope}` | counter | Quota/rate-limit denials |
| `rocket_auth_failures_total{reason}` | counter | Requests rejected for missing / invalid credentials |
| `rocket_turn_{queue_depth,active,concurrency_limit}` | gauge | Backpressure |
| `rocket_docker_blocking_calls` | gauge | Docker SDK calls running or queued on the driver's own thread pool |

Ready-to-use artifacts live alongside this page in `doc/operate/`:

- **Alert rules:** `observability-alerts.yaml` — a Prometheus Operator `PrometheusRule` covering availability, TTFT/Turn p95, cache hit ratio, cost spikes, queue backlog, code-index footprint and quota rejections.
- **Grafana dashboard:** `grafana-dashboard.json` — importable dashboard (uid `rocket-chat-ops`) for TTFT, turn throughput, cache ratio, tool/model activity, code-index footprint, backpressure and quota rejects.

### Prompt Cache Telemetry (`rocket_prompt_cache_hit_ratio`)

The `rocket_prompt_cache_hit_ratio` gauge tracks `cache_read_tokens ÷ prompt_tokens` across model dispatches. On multi-step agent turns, target hit ratio is $\ge 60\%$.
- **Request Layout:** To maximize prefix caching, requests maintain a byte-stable prefix: system prompt first, tool definitions in stable order (built-ins, dynamic, then MCP tools by server and name), followed by conversation history and a rolling cache breakpoint. Volatile items (turn checklist, reminders, dynamic images) ride at the tail.
- **Sticky Session Routing:** Outbound completions set the session ID on `user` and `prompt_cache_key`, enabling upstream providers and proxies (such as OpenRouter) to route subsequent turn steps to the same GPU worker.
- **Cache-Aware Cost:** Cached prompt reads benefit from provider-native discounts (e.g., 90% discount on Anthropic Claude and DeepSeek), tracked per call and recorded in the tenant usage ledger.

Backpressure is bounded process-wide by `ROCKET_GLOBAL_CONCURRENCY_LIMIT` (default 16) with the waiting depth exposed as `rocket_turn_queue_depth`; each session is additionally guarded by a single-writer lock (`ROCKET_SESSION_LOCK_CAPACITY`, default 256).

### Logs and traces

- **Correlated logs.** With `backend.logFormat: json` every line is one JSON object carrying `request_id`, `org_id`, `user_id`, `session_id` and (when tracing is on) `trace_id` / `span_id`, so one turn can be followed across the API, tool and sandbox layers by filtering on `session_id`. Every response carries an `X-Request-ID` header (a well-formed inbound id from your gateway is kept). Credentials, tool arguments and command lines are never logged.
- **Traces.** Set `observability.otlpEndpoint` (any OTLP/HTTP collector: Tempo, Jaeger, Honeycomb, an OpenTelemetry Collector) to export spans: the HTTP/WebSocket request, `agent.turn`, `agent.tool` (per tool, with its status) and `sandbox.exec` (per command, with the driver and exit code). Tool arguments and command lines are deliberately not span attributes. Headers, sampling and resource attributes use the standard `OTEL_*` variables (put credentials such as `OTEL_EXPORTER_OTLP_HEADERS` in a secret). Without an endpoint no exporter is started and spans are no-ops.
- **Authentication failures** are counted in `rocket_auth_failures_total{reason}` (alert `RocketAuthFailuresHigh`) and logged with method, path and client address; failed password sign-ins are additionally audit events.

### Session Storage and Secret Redaction

Starting with this release, tool execution outputs are redacted immediately at execution time in `ToolDispatcher` before being appended to `session_messages` or emitted as events. This guarantees that unredacted secrets (such as API keys or access tokens) never enter persisted conversation history, events, or subsequent prompts. In environments running pre-release deployments, existing unredacted session tables must be wiped or truncated before upgrade (`TRUNCATE sessions, session_messages, turn_traces CASCADE;`).

