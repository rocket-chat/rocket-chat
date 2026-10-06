-- Enable vector extension for embeddings if available
CREATE EXTENSION IF NOT EXISTS vector;

-- Ensure application user exists for Row-Level Security (RLS) enforcement.
-- Connecting as non-superuser rocket_app ensures PostgreSQL kernel enforces RLS policies.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rocket_app') THEN
        CREATE ROLE rocket_app WITH LOGIN PASSWORD 'postgres_prod_password';
    ELSE
        ALTER ROLE rocket_app WITH PASSWORD 'postgres_prod_password';
    END IF;
END
$$;

GRANT ALL PRIVILEGES ON DATABASE rocket_chat TO rocket_app;
GRANT ALL PRIVILEGES ON SCHEMA public TO rocket_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO rocket_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO rocket_app;
