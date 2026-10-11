"""Persisted usage ledger and BYOK endpoints.

Revision ID: 014_usage_events
Revises: 013_slack_pairing_codes
Create Date: 2026-10-10 18:00:00.000000

``usage_events`` is the append-only record of every model call's tokens and cost; budgets,
dashboards and Slack workspace spend are computed from it. It is protected by row-level
security like every tenant table. ``byok_credentials.api_base`` lets a stored key point at a
self-hosted or proxied endpoint (Ollama, vLLM, an OpenAI-compatible gateway).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "014_usage_events"
down_revision: str | None = "013_slack_pairing_codes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("user_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("session_id", sa.String(length=64), nullable=True, index=True),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_creation_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("key_source", sa.String(length=16), nullable=False, server_default="platform"),
        sa.Column("created_at", sa.BigInteger(), nullable=False, index=True),
    )
    op.add_column("byok_credentials", sa.Column("api_base", sa.String(length=500), nullable=True))

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE usage_events ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE usage_events FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_usage_events ON usage_events
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation_usage_events ON usage_events;")
        op.execute("ALTER TABLE usage_events DISABLE ROW LEVEL SECURITY;")
    op.drop_column("byok_credentials", "api_base")
    op.drop_table("usage_events")
