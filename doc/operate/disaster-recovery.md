# Disaster Recovery Runbook

This runbook is the operative companion to the backup strategy in
[Production Hardening](/operate/hardening). It states what is protected, the recovery
targets, the exact restore and migration-reversal procedures, and the recorded output of the
drill that proves those procedures work on this release. The drill is executable: it is
`scripts/dr_drill.py` in the repository root, not a prose promise.

---

## 1. Recovery Objectives & Assets

| Asset | Where it lives | Backup mechanism | Loss impact |
| :--- | :--- | :--- | :--- |
| Tenant data (sessions, traces, memories, audit) | PostgreSQL 16 | Logical `pg_dump` + managed-DB PITR | Conversation history and governance trail lost for the window |
| Attachment blobs | `ROCKET_ATTACHMENTS_DIR` volume / workspace PVC | Volume snapshot (cloud CSI) | Uploaded files unreadable; rows remain and point at missing bytes |
| Model credentials / user credentials | PostgreSQL, AES-256-GCM / Fernet encrypted | Follows the database | **Unrecoverable without the master key** — see §6 |
| Container images & Helm chart | GHCR (`ghcr.io/rocket-chat/...`) | Immutable tagged artifacts + SBOM | Redeploy the last known-good tag |
| Kubernetes secrets | Cluster secret store / external vault | Vault replication / external-secrets | Reread from the vault; never only in-cluster |

**Targets.** A logical dump completes in well under a second on the reference dataset, so set
RPO from the backup schedule (hourly logical + continuous PITR on managed PostgreSQL) and RTO
from the restore timing in §5 (single-digit seconds for the database layer, plus image pull and
pod readiness).

---

## 2. What Must Be Backed Up Together

A PostgreSQL dump alone is **not** a complete recovery. Restore these in order:

1. **`ENCRYPTION_MASTER_KEY` and `CREDENTIALS_ENCRYPTION_KEY`** (and `ROCKET_ATTACHMENT_SIGNING_KEY`)
   from the secret vault — a restored database whose ciphertext cannot be decrypted is worthless.
2. **The PostgreSQL database**.
3. **The attachment volume** captured at or before the database snapshot, so rows and blobs stay
   consistent.
4. **The exact container image tag** recorded in the release (and its SBOM), so the schema
   revision the dump expects matches the code that reads it.

---

## 3. Backup Commands

Custom-format logical backup (compressible, parallel-restorable):

```bash
pg_dump --dbname="$ADMIN_DATABASE_URL" --format=custom \
  --file="/backups/rocket_chat_$(date +%F_%H%M).dump"
```

> [!TIP]
> Match the `pg_dump`/`pg_restore` **client major version** to the server. A newer client can
> emit session GUCs an older server rejects (for example `SET transaction_timeout = 0;` from a
> PostgreSQL 18 client against a 16 server). When the versions cannot be matched, take a
> plain-format dump and restore with `psql`, dropping only that unsupported GUC line — exactly
> the fallback `scripts/dr_drill.py` implements.

For managed databases, also enable continuous WAL archiving / point-in-time recovery (Aurora,
Cloud SQL, Azure Flexible Server) and take a daily logical dump as the portable, provider-neutral
copy. See the storage class guidance in [Cloud Storage & CSI](/operate/cloud-storage).

---

## 4. Restore Runbook

Run as the database superuser / schema owner; the application role (`rocket_app`) is subject to
forced RLS and cannot create the schema.

```bash
# 1. Create a fresh database (never restore over the live one).
createdb "rocket_chat_restore"

# 2. Restore the dump.
pg_restore --no-owner --no-privileges \
  --dbname="$ADMIN_DATABASE_URL_BASE/rocket_chat_restore" \
  /backups/rocket_chat_2026-10-09_0200.dump

# 3. Prove the schema revision — a restore that cannot state its revision is an unverified restore.
DATABASE_URL="postgresql+asyncpg://…/rocket_chat_restore" uv run alembic upgrade head
DATABASE_URL="postgresql+asyncpg://…/rocket_chat_restore" uv run alembic current
# expected: 009_session_messages (head)
```

Then point the control plane at the restored database (update the `DATABASE_URL` /
`ADMIN_DATABASE_URL` secret), roll the backend deployment, and confirm `/v1/ready` returns
**200** before shifting traffic. Attachment volume is mounted at the same
`ROCKET_ATTACHMENTS_DIR` path; keep the database snapshot and volume snapshot within the same
backup window so `attachments.workspace_path` rows resolve.

---

## 5. Recorded Drill (proves the procedure)

`scripts/dr_drill.py` builds a scratch database at head, seeds a row, dumps it, restores it into
a second scratch database, checks the revision, table set and seeded row, then reverses the full
migration chain. Output of the recorded run:

```text
== 1. Backup / restore round trip ==
  [   704.4 ms] alembic upgrade head on rocket_chat_backup
  [    82.4 ms] backup (plain) rocket_chat_backup -> rocket_chat_drill.dump
  [   249.6 ms] restore (plain) rocket_chat_drill.dump -> rocket_chat_restore
  [   354.1 ms] alembic upgrade head on rocket_chat_restore (no-op proof)
  dump size: 52134 bytes
  revision: source=009_session_messages (head) restore=009_session_messages (head)
  public base tables: source=23 restore=23
  seeded organizations row restored: 1

== 2. Reversible migration chain (per revision) ==
  chain: 001_initial -> 002_user_and_org_scoping -> 003_turn_traces -> 004_session_flags -> 005_attachments -> 006_memories -> 007_slack_quotas -> 008_audit -> 009_session_messages
  [   408.8 ms] upgrade +1 -> 001_initial
  [   478.1 ms] upgrade +1 -> 002_user_and_org_scoping
  [   352.8 ms] upgrade +1 -> 003_turn_traces
  [   428.7 ms] upgrade +1 -> 004_session_flags
  [   377.5 ms] upgrade +1 -> 005_attachments
  [   365.7 ms] upgrade +1 -> 006_memories
  [   351.5 ms] upgrade +1 -> 007_slack_quotas
  [   356.7 ms] upgrade +1 -> 009_session_messages
  [   349.2 ms] downgrade -1 from 009_session_messages
  [   340.6 ms] downgrade -1 from 007_slack_quotas
  [   350.7 ms] downgrade -1 from 006_memories
  [   359.2 ms] downgrade -1 from 005_attachments
  [   341.5 ms] downgrade -1 from 004_session_flags
  [   335.5 ms] downgrade -1 from 003_turn_traces
  [   367.2 ms] downgrade -1 from 002_user_and_org_scoping
  [   357.9 ms] downgrade -1 from 001_initial

== 3. Full chain cycle ==
  [   588.9 ms] upgrade head
  [   424.0 ms] downgrade base
  [   565.6 ms] upgrade head (again)
  after downgrade base, public base tables = 1
  full cycle head: 009_session_messages (head) -> base -> 009_session_messages (head)

== Drill summary ==
  backup/restore: revision=009_session_messages (head) tables=24 dump=52134 bytes
  migration chain: 8 revisions each reversed cleanly
  scratch databases kept: rocket_chat_backup, rocket_chat_restore, rocket_chat_drill_mig
```

Run it before every release against the release branch:

```bash
uv run python scripts/dr_drill.py
```

---

## 6. Secret Recovery & Rotation

Secrets are the part of recovery that backups cannot fix. The full rotation procedure lives in
[Security & Compliance](/administer/security); in the context of disaster recovery:

* **`ENCRYPTION_MASTER_KEY`** decrypts the stored model credentials (organization and member keys). Losing it permanently loses every
  encrypted credential — there is no recovery path. Restore it from the vault *before* restoring
  the database.
* Rotating it is a **re-encryption migration**, not a config change: use
  `scripts/rotate_master_key.py` to decrypt with the old key and re-encrypt with the new one
  (`--apply` to persist; dry-run by default).
* **`CREDENTIALS_ENCRYPTION_KEY`** protects user OAuth/PAT tokens (Fernet). Rotating it has the
  same all-or-nothing effect.
* **`ROCKET_ATTACHMENT_SIGNING_KEY`** only signs short-lived retrieval tokens; rotating it
  invalidates outstanding preview links and nothing else.

---

## 7. Failure Playbooks

| Failure | Action |
| :--- | :--- |
| **Database unavailable, data intact** | Keep `/v1/health` (liveness) green, let `/v1/ready` return 503 so pods drain; fail over to the standby; verify `alembic current` = `009_session_messages (head)`. |
| **Bad migration on a live database** | `alembic downgrade -1` on the schema-owner connection to return to the previous revision (every revision in `001`–`008` is reversible, §5), then redeploy the matching image. |
| **Corrupted / lost database** | Restore the latest dump into a fresh database, or PITR to just before the incident; reconcile attachments from the volume snapshot; restore secrets first. |
| **One pod loss** | Kubernetes reschedules; the PodDisruptionBudget (`minAvailable: 1`) keeps the last backend pod from voluntary eviction, and session affinity returns clients to a surviving pod. |
| **Region loss** | Redeploy the Helm chart in the DR region against the restored managed database and read secrets from the replicated vault; pull the last release image tag and its SBOM from GHCR. |

---

## 8. Recovery Verification Checklist

Before returning traffic to a restored environment:

- [ ] `alembic current` reports `009_session_messages (head)`.
- [ ] The public table count matches the source (`23` at this release).
- [ ] A known tenant can read a session, its turn traces and its audit rows.
- [ ] Cross-tenant reads return nothing (RLS still forced after restore).
- [ ] `GET /v1/ready` returns `200`; `GET /metrics` scrapes.
- [ ] A stored model credential decrypts (proves the correct master key was restored).
- [ ] An existing attachment retrieves (database row and volume blob agree).
