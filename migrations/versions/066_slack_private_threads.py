"""Slack private requests run in a thread of the requester's direct messages.

Revision ID: 066_slack_private_threads
Revises: 065_team_templates
Create Date: 2026-11-05 10:00:00.000000

``slack_private_threads`` names the private session of each DM thread: a stop or a reply
there carries only the DM channel and thread. The two bot toggles that sent a private
request's messages by DM (``private_dm_result``, ``private_dm_updates``) are dropped, as the
DM thread is now the only private mode.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "066_slack_private_threads"
down_revision: str | None = "065_team_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "slack_private_threads"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dm_channel_id", sa.String(length=64), nullable=False),
        sa.Column("dm_thread_ts", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("slack_user_id", sa.String(length=64), nullable=False),
        sa.Column("origin_channel_id", sa.String(length=64), nullable=False),
        sa.Column("origin_thread_ts", sa.String(length=64), nullable=False),
        sa.Column(
            "origin_channel_type", sa.String(length=20), nullable=False, server_default="channel"
        ),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("org_id", "dm_channel_id", "dm_thread_ts"),
    )
    op.drop_column("slack_bots", "private_dm_updates")
    op.drop_column("slack_bots", "private_dm_result")
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
    op.add_column(
        "slack_bots",
        sa.Column("private_dm_result", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "slack_bots",
        sa.Column("private_dm_updates", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{_TABLE} ON {_TABLE};")
    op.drop_table(_TABLE)
