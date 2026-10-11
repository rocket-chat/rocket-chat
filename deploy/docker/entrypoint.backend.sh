#!/usr/bin/env bash
set -e

# Single-container deployments (compose, local) migrate on start. Kubernetes runs the same
# script in an init container and sets ROCKET_RUN_MIGRATIONS=false here, so replicas never
# race each other or hold schema-owner credentials longer than necessary.
if [ "${ROCKET_RUN_MIGRATIONS:-true}" != "false" ]; then
    /app/deploy/docker/migrate.sh
fi

echo "[backend] Starting Rocket Chat Control Plane: $@"
exec "$@"
