"""Session files: offloaded tool outputs and sandbox-free scratch files, kept in PostgreSQL.

Revision ID: 067_session_files
Revises: 066_slack_private_threads
Create Date: 2026-11-17 10:00:00.000000

A tool result too large for the conversation used to be kept in the API process's memory when
no sandbox was running: lost on a restart and invisible to another replica. ``session_files``
keeps it with the session, bounded per file and per session, and is copied into the workspace
when a sandbox starts later.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "067_session_files"
down_revision: str | None = "066_slack_private_threads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "session_files"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "session_id",
            sa.String(length=64),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="output"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False, index=True),
        sa.Column("written_ms", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("session_id", "path"),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation_{_TABLE} ON {_TABLE}
            FOR ALL
            USING (org_id = current_setting('app.current_org_id', true));
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{_TABLE} ON {_TABLE};")
    op.drop_table(_TABLE)
