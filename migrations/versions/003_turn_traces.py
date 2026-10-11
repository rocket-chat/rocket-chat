"""Durable turn traces with PostgreSQL Row-Level Security (spec section 8.1.2).

Revision ID: 003_turn_traces
Revises: 002_user_and_org_scoping
Create Date: 2026-10-12 09:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "003_turn_traces"
down_revision: str | None = "002_user_and_org_scoping"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "turn_traces",
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
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.BigInteger(), nullable=False),
        # Nullable so a turn can leave a durable `running` marker at start: without it a
        # process death mid-turn would leave no evidence to recover.
        sa.Column("ended_at", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reasoning_summary", sa.Text(), nullable=True),
        sa.Column("tool_calls", _JSONB, nullable=False),
        sa.Column("diff_paths", _JSONB, nullable=False),
        sa.Column("approvals", _JSONB, nullable=False),
        sa.Column("tokens", _JSONB, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_turn_traces_session_turn", "turn_traces", ["session_id", "turn_index"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE turn_traces ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE turn_traces FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_turn_traces ON turn_traces
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation_turn_traces ON turn_traces;")
        op.execute("ALTER TABLE turn_traces DISABLE ROW LEVEL SECURITY;")

    op.drop_index("ix_turn_traces_session_turn", table_name="turn_traces")
    op.drop_table("turn_traces")
