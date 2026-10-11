"""Durable LLM transcript with PostgreSQL Row-Level Security.

Revision ID: 009_session_messages
Revises: 008_audit
Create Date: 2026-10-10 09:00:00.000000

Append-only ``session_messages`` keeps the exact message list the model saw (including
assistant tool calls and tool results), ordered by ``seq`` per session. The orchestrator
rehydrates from it after a restart, a fork or on another replica.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "009_session_messages"
down_revision: str | None = "008_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "session_messages",
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
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_calls", _JSONB, nullable=True),
        sa.Column("tool_call_id", sa.String(length=128), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("session_id", "seq", name="uq_session_messages_seq"),
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE session_messages ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE session_messages FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_session_messages ON session_messages
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation_session_messages ON session_messages;")
        op.execute("ALTER TABLE session_messages DISABLE ROW LEVEL SECURITY;")
    op.drop_table("session_messages")
