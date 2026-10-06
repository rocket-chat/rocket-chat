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
