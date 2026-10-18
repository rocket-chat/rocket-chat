"""Session waits table with Row-Level Security for durable wake-ups.

Revision ID: 039_session_waits
Revises: 038_session_artifacts
Create Date: 2026-10-18 01:00:00.000000

Persists durable session wait requests (timers, background process exits, GitHub CI/reviews)
with forced Row-Level Security, surviving process restarts and leader failovers.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "039_session_waits"
down_revision: str | None = "038_session_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_waits",
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
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("deadline", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="waiting"),
        sa.Column("wake_prompt", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("claimed_at", sa.BigInteger(), nullable=True),
        sa.Column("completed_at", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_session_waits_status_deadline",
        "session_waits",
        ["status", "deadline"],
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE session_waits ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE session_waits FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_session_waits ON session_waits
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation_session_waits ON session_waits;")
    op.drop_index("ix_session_waits_status_deadline", table_name="session_waits")
    op.drop_table("session_waits")
