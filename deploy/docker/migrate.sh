#!/usr/bin/env bash
# Apply database migrations as the schema owner. Safe to run from many replicas at once:
# Alembic's env.py serialises runs with a PostgreSQL advisory lock.
set -euo pipefail

MIGRATION_URL="${ADMIN_DATABASE_URL:-${DATABASE_URL:-}}"
if [ -z "$MIGRATION_URL" ]; then
    echo "[migrate] No ADMIN_DATABASE_URL or DATABASE_URL set; nothing to migrate."
    exit 0
fi

MAX_RETRIES="${MIGRATION_MAX_RETRIES:-30}"
COUNT=0
echo "[migrate] Applying database migrations via Alembic..."
until DATABASE_URL="$MIGRATION_URL" alembic upgrade head; do
    COUNT=$((COUNT + 1))
    if [ "$COUNT" -ge "$MAX_RETRIES" ]; then
        echo "[migrate] ERROR: migrations did not apply after $MAX_RETRIES attempts." >&2
        exit 1
    fi
    echo "[migrate] Waiting for the database ($COUNT/$MAX_RETRIES)..."
    sleep 2
done
echo "[migrate] Database schema is at head."
