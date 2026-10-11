"""Long-term memories and session summaries with RLS (spec section 8.3).

Revision ID: 006_memories
Revises: 005_attachments
Create Date: 2026-10-13 10:10:00.000000

``memories.embedding`` uses the same 1536-dim pgvector component as ``knowledge_chunks``
so ``recall`` can reuse the existing cosine-search helpers. ``session_summaries`` records
which turn range a summary covers so it is never mistaken for the full history.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    Vector = None

# revision identifiers, used by Alembic.
revision: str = "006_memories"
down_revision: str | None = "005_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    vector_type = Vector(1536) if (is_postgres and Vector is not None) else sa.JSON()

    op.create_table(
        "memories",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # Nullable so an org-scoped memory has no single owner.
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("scope", sa.String(length=20), nullable=False, server_default="user", index=True),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="fact", index=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", vector_type, nullable=False),
        sa.Column(
            "source_session_id",
            sa.String(length=64),
            sa.ForeignKey("sessions.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("last_used_at", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_memories_org_user_scope",
        "memories",
        ["org_id", "user_id", "scope"],
    )

    op.create_table(
        "session_summaries",
        sa.Column(
            "session_id",
            sa.String(length=64),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("embedding", vector_type, nullable=False),
        sa.Column("turn_start", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("turn_end", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )

    if is_postgres:
        for table in ("memories", "session_summaries"):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
            op.execute(
                f"""
                CREATE POLICY tenant_isolation_{table} ON {table}
                    FOR ALL
                    USING (org_id = current_setting('app.current_org_id', true));
                """
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in ("memories", "session_summaries"):
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table};")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")

    op.drop_table("session_summaries")
    op.drop_index("ix_memories_org_user_scope", table_name="memories")
    op.drop_table("memories")
