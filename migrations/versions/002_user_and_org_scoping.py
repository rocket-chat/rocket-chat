"""User and org scoping migration for MCP servers, skills, knowledge bases, agent personas, and settings.

Revision ID: 002_user_and_org_scoping
Revises: 001_initial
Create Date: 2026-10-08 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    Vector = None

# revision identifiers, used by Alembic.
revision: str = "002_user_and_org_scoping"
down_revision: str | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # 1. org_settings
    op.create_table(
        "org_settings",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("domain", sa.String(length=64), nullable=False),
        sa.Column(
            "config", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column(
            "locked_keys", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("uq_org_settings_domain", "org_settings", ["org_id", "domain"], unique=True)

    # 2. user_settings
    op.create_table(
        "user_settings",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("domain", sa.String(length=64), nullable=False),
        sa.Column(
            "overrides", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("uq_user_settings_domain", "user_settings", ["user_id", "domain"], unique=True)

    # 3. user_credentials
    op.create_table(
        "user_credentials",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=False),
        sa.Column(
            "metadata_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "uq_user_credentials_provider",
        "user_credentials",
        ["user_id", "provider"],
        unique=True,
    )

    # 4. knowledge_bases
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("scope", sa.String(length=20), nullable=False, server_default="org", index=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "vector_index",
            sa.String(length=100),
            nullable=False,
            server_default="pgvector_hnsw_default",
        ),
        sa.Column(
            "embedding_model",
            sa.String(length=100),
            nullable=False,
            server_default="text-embedding-3-small (1536d)",
        ),
        sa.Column("hybrid_search", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )

    # 5. knowledge_documents
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "kb_id",
            sa.String(length=64),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False, server_default="text"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "metadata_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )

    # 6. knowledge_chunks
    vector_type = Vector(1536) if (is_postgres and Vector is not None) else sa.JSON()
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "doc_id",
            sa.String(length=64),
            sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "kb_id",
            sa.String(length=64),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", vector_type, nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "metadata_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_knowledge_chunks_kb", "knowledge_chunks", ["kb_id", "chunk_index"])

    # 7. custom_skills
    op.create_table(
        "custom_skills",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("scope", sa.String(length=20), nullable=False, server_default="org", index=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("runtime", sa.String(length=50), nullable=True, server_default="text"),
        sa.Column("signature", sa.String(length=255), nullable=True, server_default=""),
        sa.Column("code", sa.Text(), nullable=True, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "ix_custom_skills_org_user",
        "custom_skills",
        ["org_id", "user_id", "name"],
    )

    # 8. mcp_servers
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("scope", sa.String(length=20), nullable=False, server_default="org", index=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("transport", sa.String(length=50), nullable=False, server_default="stdio"),
        sa.Column("endpoint_or_command", sa.Text(), nullable=False),
        sa.Column("guidance", sa.Text(), nullable=False, server_default=""),
        sa.Column("auth_type", sa.String(length=50), nullable=False, server_default="none"),
        sa.Column(
            "encrypted_auth",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "tools",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="connected"),
        sa.Column("last_probed", sa.String(length=100), nullable=False, server_default="Just now"),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "ix_mcp_servers_org_user",
        "mcp_servers",
        ["org_id", "user_id", "name"],
    )

    # 9. agent_personas
    op.create_table(
        "agent_personas",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("scope", sa.String(length=20), nullable=False, server_default="org", index=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role_title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column(
            "whitelisted_tools",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "skill_ids",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "knowledge_base_ids",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("icon", sa.String(length=50), nullable=False, server_default="bot"),
        sa.Column(
            "sandbox_image",
            sa.String(length=255),
            nullable=False,
            server_default="python:3.12-slim",
        ),
        sa.Column("cpu_limit", sa.String(length=50), nullable=False, server_default="2.0"),
        sa.Column("memory_limit", sa.String(length=50), nullable=False, server_default="4Gi"),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("thinking_budget_tokens", sa.Integer(), nullable=True),
        sa.Column("reasoning_effort", sa.String(length=50), nullable=True),
        sa.Column("max_turns", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )

    # 10. subagent_configs
    op.create_table(
        "subagent_configs",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role_title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("max_turns", sa.Integer(), nullable=False, server_default="5"),
        sa.Column(
            "whitelisted_tools",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.2"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )

    # 11. Apply RLS on Postgres
    if is_postgres:
        tables_to_rls = [
            "org_settings",
            "user_settings",
            "user_credentials",
            "knowledge_bases",
            "knowledge_documents",
            "knowledge_chunks",
            "custom_skills",
            "mcp_servers",
            "agent_personas",
            "subagent_configs",
        ]
        for tbl in tables_to_rls:
            op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;")
            op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY;")
            op.execute(f"""
                CREATE POLICY tenant_isolation_{tbl} ON {tbl}
                    FOR ALL
                    USING (org_id = current_setting('app.current_org_id', true));
            """)


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    tables_to_drop = [
        "subagent_configs",
        "agent_personas",
        "mcp_servers",
        "custom_skills",
        "knowledge_chunks",
        "knowledge_documents",
        "knowledge_bases",
        "user_credentials",
        "user_settings",
        "org_settings",
    ]

    if is_postgres:
        for tbl in tables_to_drop:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{tbl} ON {tbl};")
            op.execute(f"ALTER TABLE {tbl} DISABLE ROW LEVEL SECURITY;")

    for tbl in tables_to_drop:
        op.drop_table(tbl)
