"""Compaction checkpoints for crash-resilient session rehydration with Row-Level Security.

Revision ID: 033_session_compactions
Revises: 032_agent_sandbox_type
Create Date: 2026-10-17 01:00:00.000000

Stores compaction checkpoints (summary, active window boundary, running context)
in PostgreSQL with forced RLS. If an API or sandbox pod crashes, the session rehydrates
completely from PostgreSQL (latest checkpoint + subsequent session_messages) without
losing history or recompacting from scratch.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "033_session_compactions"
down_revision: str | None = "032_agent_sandbox_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "session_compactions",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "session_id",
            sa.String(length=64),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
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
        sa.Column("boundary_seq", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("compacted_messages", _JSONB, nullable=False),
        sa.Column("tokens_before", sa.Integer(), nullable=False),
        sa.Column("tokens_after", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("session_id", "boundary_seq", name="uq_session_compactions_boundary"),
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE session_compactions ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE session_compactions FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_session_compactions ON session_compactions
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP POLICY IF EXISTS tenant_isolation_session_compactions ON session_compactions;"
        )
        op.execute("ALTER TABLE session_compactions DISABLE ROW LEVEL SECURITY;")
    op.drop_table("session_compactions")
