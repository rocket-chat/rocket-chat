#!/usr/bin/env bash
set -e

# Wait for PostgreSQL to be ready if DATABASE_URL or ADMIN_DATABASE_URL is provided
if [ -n "$DATABASE_URL" ] || [ -n "$ADMIN_DATABASE_URL" ]; then
    echo "[backend] Database URL detected. Running database migration check..."
    
    # Run alembic migrations using alembic upgrade head
    # Use ADMIN_DATABASE_URL if provided (superuser for DDL/RLS creation), otherwise DATABASE_URL
    MIGRATION_URL="${ADMIN_DATABASE_URL:-$DATABASE_URL}"
    echo "[backend] Applying database migrations via Alembic..."
    
    # Simple retry loop in case database is still booting
    MAX_RETRIES=30
    COUNT=0
    until DATABASE_URL="$MIGRATION_URL" alembic upgrade head || [ $COUNT -ge $MAX_RETRIES ]; do
        COUNT=$((COUNT + 1))
        echo "[backend] Waiting for database to become available ($COUNT/$MAX_RETRIES)..."
        sleep 2
    done

    if [ $COUNT -ge $MAX_RETRIES ]; then
        echo "[backend] ERROR: Timed out waiting for database migration!"
        exit 1
    fi
    echo "[backend] Database migrations applied successfully."
fi

echo "[backend] Starting Rocket Chat Control Plane: $@"
exec "$@"
