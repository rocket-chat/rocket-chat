"""Messages sent while a turn runs, kept until a turn takes them, with Row-Level Security.

Revision ID: 052_session_queued_messages
Revises: 051_repository_setups
Create Date: 2026-10-21 21:00:00.000000

A message sent during a turn either steers it (taken at its next step) or is queued for after
it. Both lived in the process's memory: a restart lost them, and another replica could not see
them. Rows go from ``pending`` to ``taken`` (steered into the running turn), ``started`` (run as
the next turn) or ``cancelled`` (withdrawn by the person).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "052_session_queued_messages"
down_revision: str | None = "051_repository_setups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_queued_messages",
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
        sa.Column("author_user_id", sa.String(length=64), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("delivery", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "ix_session_queued_messages_status", "session_queued_messages", ["status", "created_at"]
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE session_queued_messages ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE session_queued_messages FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_session_queued_messages ON session_queued_messages
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP POLICY IF EXISTS tenant_isolation_session_queued_messages "
            "ON session_queued_messages;"
        )
    op.drop_index("ix_session_queued_messages_status", table_name="session_queued_messages")
    op.drop_table("session_queued_messages")
