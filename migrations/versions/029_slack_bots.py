"""Several Slack bots per organization, each its own Slack app, with a router decision log.

Revision ID: 029_slack_bots
Revises: 028_agent_tool_loading
Create Date: 2026-10-15 18:00:00.000000

``slack_bots`` replaces the deployment's single Slack app (``SLACK_BOT_TOKEN``/``SLACK_APP_TOKEN``):
each row holds its sealed tokens, agent or router, response mode and policy overrides.
``slack_route_decisions`` records what each router chose. Both are protected by forced
row-level security.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "029_slack_bots"
down_revision: str | None = "028_agent_tool_loading"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_TABLES = ("slack_bots", "slack_route_decisions")


def _org_column() -> sa.Column:
    return sa.Column(
        "org_id",
        sa.String(length=64),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


def upgrade() -> None:
    op.create_table(
        "slack_bots",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        _org_column(),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("bot_token_encrypted", sa.Text(), nullable=True),
        sa.Column("app_token_encrypted", sa.Text(), nullable=True),
        sa.Column("bot_token_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("app_token_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("team_id", sa.String(length=64), nullable=True),
        sa.Column("team_name", sa.String(length=255), nullable=True),
        sa.Column("bot_user_id", sa.String(length=64), nullable=True),
        sa.Column("app_id", sa.String(length=64), nullable=True),
        sa.Column("handle", sa.String(length=100), nullable=True),
        sa.Column("agent_id", sa.String(length=64), nullable=True),
        sa.Column("response_mode", sa.String(length=16), nullable=False, server_default="thread"),
        sa.Column("respond_in_dms", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("listen_channels", _JSON, nullable=False, server_default="[]"),
        sa.Column(
            "private_reaction",
            sa.String(length=64),
            nullable=False,
            server_default="white_check_mark",
        ),
        sa.Column("private_dm_result", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("router", _JSON, nullable=True),
        sa.Column("policy", _JSON, nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "slack_route_decisions",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        _org_column(),
        sa.Column(
            "bot_id",
            sa.String(length=64),
            sa.ForeignKey("slack_bots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel_id", sa.String(length=64), nullable=False),
        sa.Column("thread_ts", sa.String(length=64), nullable=False),
        sa.Column("slack_user_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("excerpt", sa.Text(), nullable=False, server_default=""),
        sa.Column("route", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("applied", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "ix_slack_route_decisions_bot_time", "slack_route_decisions", ["bot_id", "created_at"]
    )
    if op.get_bind().dialect.name != "postgresql":
        return
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
    if op.get_bind().dialect.name == "postgresql":
        for table in _TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table};")
    op.drop_index("ix_slack_route_decisions_bot_time", table_name="slack_route_decisions")
    op.drop_table("slack_route_decisions")
    op.drop_table("slack_bots")
