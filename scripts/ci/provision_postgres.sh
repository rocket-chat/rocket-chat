#!/usr/bin/env bash
# Prepare the CI PostgreSQL the way production has it: an unprivileged application role (row-level
# security does not apply to a superuser), the pgvector extension, and default grants.
# Usage: provision_postgres.sh [host]   (PGPASSWORD must be exported)
set -euo pipefail
host="${1:-localhost}"
run() { psql -h "$host" -U postgres -d rocket_chat -v ON_ERROR_STOP=1 -c "$1" >/dev/null; }

run "CREATE ROLE rocket_app LOGIN PASSWORD 'rocket_app';"
run "CREATE EXTENSION IF NOT EXISTS vector;"
run "GRANT ALL PRIVILEGES ON DATABASE rocket_chat TO rocket_app;"
run "GRANT ALL ON SCHEMA public TO rocket_app;"
run "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO rocket_app;"
run "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO rocket_app;"
