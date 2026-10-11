"""Background processes started in a session's sandbox.

Revision ID: 023_sandbox_processes
Revises: 022_turn_trace_pending_decision
Create Date: 2026-10-11 14:00:00.000000

The sandbox is the source of truth for a running process; this table keeps the history after
the sandbox hibernates or is replaced, and lets the cockpit and the agent see what ran.
Protected by row-level security.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "023_sandbox_processes"
down_revision: str | None = "022_turn_trace_pending_decision"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sandbox_processes",
        sa.Column(
            "session_id",
            sa.String(length=64),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("process_id", sa.String(length=16), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.BigInteger(), nullable=False),
        sa.Column("ended_at", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE sandbox_processes ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE sandbox_processes FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_sandbox_processes ON sandbox_processes
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation_sandbox_processes ON sandbox_processes;")
        op.execute("ALTER TABLE sandbox_processes DISABLE ROW LEVEL SECURITY;")
    op.drop_table("sandbox_processes")
