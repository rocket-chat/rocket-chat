#!/bin/sh
# First-boot database setup for the production compose stack.
#
# The API connects as the unprivileged `rocket_app` role so PostgreSQL enforces row-level
# security (a superuser bypasses it). Its password comes from the environment instead of
# being baked into the image or a SQL file.
set -eu

: "${ROCKET_APP_DB_PASSWORD:?ROCKET_APP_DB_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 -v app_password="$ROCKET_APP_DB_PASSWORD" \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;

SELECT format('CREATE ROLE rocket_app WITH LOGIN PASSWORD %L', :'app_password')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rocket_app') \gexec
SELECT format('ALTER ROLE rocket_app WITH PASSWORD %L', :'app_password') \gexec

-- Least privilege: the API reads and writes rows but never changes the schema (Alembic, run
-- as the owner, does). Tables the owner creates later are granted automatically.
GRANT CONNECT ON DATABASE rocket_chat TO rocket_app;
GRANT USAGE ON SCHEMA public TO rocket_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO rocket_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO rocket_app;
SQL
