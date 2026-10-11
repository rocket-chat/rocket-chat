"""Per-workspace Slack quotas and counters with RLS (spec section 8.4).

Revision ID: 007_slack_quotas
Revises: 006_memories
Create Date: 2026-10-13 10:15:00.000000

One row per Slack workspace (``team_id``), holding both the configured caps and the
mutable counters the pre-dispatch quota check reads. RLS scopes rows by ``org_id``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "007_slack_quotas"
down_revision: str | None = "006_memories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "slack_workspace_quotas",
        sa.Column("team_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("monthly_usd_cap", sa.Float(), nullable=False, server_default="0"),
        sa.Column("turns_per_hour", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_concurrent_sessions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_month_spend_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("turns_this_hour", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hour_window_start", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE slack_workspace_quotas ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE slack_workspace_quotas FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_slack_workspace_quotas ON slack_workspace_quotas
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP POLICY IF EXISTS tenant_isolation_slack_workspace_quotas "
            "ON slack_workspace_quotas;"
        )
        op.execute("ALTER TABLE slack_workspace_quotas DISABLE ROW LEVEL SECURITY;")

    op.drop_table("slack_workspace_quotas")
