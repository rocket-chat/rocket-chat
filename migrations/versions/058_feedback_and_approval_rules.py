"""Answer feedback and remembered approvals, with Row-Level Security.

Revision ID: 058_feedback_and_approval_rules
Revises: 057_subagents_v2
Create Date: 2026-10-29 10:00:00.000000

``answer_feedback`` holds a member's thumbs up or down (and comment) on one answer, exported by
administrators for evaluation. ``approval_rules`` holds the approvals a member remembered ("for
this session", "always for this command in this repository"); the tool registry reads them before
raising a gate.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "058_feedback_and_approval_rules"
down_revision: str | None = "057_subagents_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("answer_feedback", "approval_rules")


def _org_column() -> sa.Column[str]:
    return sa.Column(
        "org_id",
        sa.String(length=64),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


def upgrade() -> None:
    op.create_table(
        "answer_feedback",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        _org_column(),
        sa.Column(
            "session_id",
            sa.String(length=64),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("turn_number", sa.Integer(), nullable=False),
        sa.Column("rating", sa.String(length=8), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("prompt_excerpt", sa.Text(), nullable=False, server_default=""),
        sa.Column("answer_excerpt", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("agent_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("session_id", "user_id", "turn_number", name="uq_answer_feedback_turn"),
    )
    op.create_table(
        "approval_rules",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        _org_column(),
        sa.Column("user_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column(
            "session_id",
            sa.String(length=64),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("repository", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("tool", sa.String(length=255), nullable=False),
        sa.Column("command", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("last_used_at", sa.BigInteger(), nullable=True),
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in _TABLES:
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
    for table in reversed(_TABLES):
        if bind.dialect.name == "postgresql":
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table};")
        op.drop_table(table)
