"""Goals and safety budgets for autonomous multi-turn continuations with Row-Level Security.

Revision ID: 034_session_goals
Revises: 033_session_compactions
Create Date: 2026-10-17 02:00:00.000000

Stores autonomous session goals (description, status, safety budgets, consumed tokens/cost,
continuation counts) in PostgreSQL with forced RLS.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "034_session_goals"
down_revision: str | None = "033_session_compactions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_goals",
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
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("max_wall_time_seconds", sa.Integer(), nullable=True),
        sa.Column("max_total_tokens", sa.Integer(), nullable=True),
        sa.Column("max_cost_usd", sa.Float(), nullable=True),
        sa.Column("consumed_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consumed_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("continuation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_continuations", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("stop_condition", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("completed_at", sa.BigInteger(), nullable=True),
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE session_goals ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE session_goals FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_session_goals ON session_goals
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation_session_goals ON session_goals;")
    op.drop_table("session_goals")
