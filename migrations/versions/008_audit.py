"""Durable governance audit trail with PostgreSQL Row-Level Security (spec section 8.6.1).

Revision ID: 008_audit
Revises: 007_slack_quotas
Create Date: 2026-10-14 09:15:00.000000

One row per tool execution, approval request/decision, model call, credential change and
settings mutation. ``approved_by`` carries the approval trail; ``redacted`` and
``redaction_count`` record that a redaction occurred without storing the removed value.
RLS scopes rows by ``org_id`` exactly like the other tenant-owned tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "008_audit"
down_revision: str | None = "007_slack_quotas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("actor_user_id", sa.String(length=64), nullable=True, index=True),
        sa.Column("session_id", sa.String(length=64), nullable=True, index=True),
        sa.Column("event_type", sa.String(length=50), nullable=False, index=True),
        sa.Column("tool_name", sa.String(length=255), nullable=True),
        sa.Column("risk_level", sa.String(length=20), nullable=True),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False, index=True),
        sa.Column("redacted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("redaction_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata_json", _JSONB, nullable=False),
    )
    op.create_index(
        "ix_audit_events_org_created",
        "audit_events",
        ["org_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE audit_events FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_audit_events ON audit_events
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation_audit_events ON audit_events;")
        op.execute("ALTER TABLE audit_events DISABLE ROW LEVEL SECURITY;")

    op.drop_index("ix_audit_events_org_created", table_name="audit_events")
    op.drop_table("audit_events")
