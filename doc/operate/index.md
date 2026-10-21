---
title: Operate
description: "Run Rocket Chat in production: deploy, upgrade, monitor, back up, recover."
---

# Operate

Rocket Chat ships as container images and a Helm chart. For a single machine, Docker Compose is enough; for production use Kubernetes.

| Environment | Packaging | Storage |
| :--- | :--- | :--- |
| Single node | `deploy/docker-compose.yml` | Docker volumes |
| Kubernetes 1.28+ | `deploy/helm/platform` (OCI chart `oci://ghcr.io/rocket-chat/charts/rocket-chat`) | Cloud CSI classes (`gp3`, `managed-csi`, `pd-balanced`) |

## Runbooks

| Runbook | Covers |
| :--- | :--- |
| [Helm chart](/operate/helm-chart) | Production values, secrets, probes, autoscaling, public URL |
| [Production hardening](/operate/hardening) | Managed PostgreSQL, migrations, replicas, draining, metrics, alerts and dashboard |
| [Disaster recovery](/operate/disaster-recovery) | Backup and restore, measured drill timings, migration reversal, failure playbooks |
| [Cloud storage](/operate/cloud-storage) | Storage classes for sandbox volumes on AWS, Azure, GCP |
| [Troubleshooting](/operate/troubleshooting) | Common deployment and runtime failures |
| [Security](/administer/security) | Tenant isolation, sandbox hardening, secret rotation |

## Artifacts

Each release publishes the backend and frontend images (multi-arch), the sandbox images (the development image and its browser variant), the Helm chart and a software bill of materials. Pin versions in production; `:latest` and `:dev` move. The backend image carries its release number in `ROCKET_VERSION`, which the settings console shows; a `:dev` image or a run from source reports `dev`.

## Day-two checklist

* `/v1/ready` (readiness, checks the database) and `/v1/health` (liveness) back your probes; `/metrics` is the Prometheus endpoint, with an alert set and a Grafana dashboard in this folder.
* Migrations run as an init step with a database lock, so several replicas can start together; a rollback restores the previous image and, if needed, reverses the migration (see Disaster recovery).
* Back up PostgreSQL (sessions, traces, settings, audit). Sandbox volumes hold working copies that can be recreated; back them up only if sessions hold unpushed work you cannot lose.
* Watch spend and rate limits (Token Usage page, `/metrics`), certificate and token expiry for GitHub and Slack, and the number of running sandboxes.
