"""Triggers, webhook delivery de-duplication and outbound webhooks, with Row-Level Security.

Revision ID: 059_automation
Revises: 058_feedback_and_approval_rules
Create Date: 2026-10-30 10:00:00.000000

``triggers`` are an organization's rules for starting sessions from events (GitHub, GitLab, Slack,
a schedule, the API, Jira, Linear). ``webhook_deliveries`` remembers each handled delivery under a
unique key, so several replicas start a session once. ``outbound_webhooks`` are an organization's
signed outcome reports, and ``outbound_deliveries`` the durable queue that retries them.
``code_embeddings`` keeps the embedding of each code chunk (by content hash and model), so code
search never embeds an unchanged chunk twice, across sessions and restarts.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

try:
    from pgvector.sqlalchemy import Vector
except ImportError:  # pragma: no cover - pgvector ships with the API
    Vector = None

revision: str = "059_automation"
down_revision: str | None = "058_feedback_and_approval_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "triggers",
    "webhook_deliveries",
    "outbound_webhooks",
    "outbound_deliveries",
    "code_embeddings",
)


def _json() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


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
        "triggers",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        _org_column(),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False, index=True),
        sa.Column("filter", _json(), nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("permission_mode", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("model_tier", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("sandbox_type", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="work"),
        sa.Column("auto_fix", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("instructions", sa.Text(), nullable=False, server_default=""),
        sa.Column("schedule", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        _org_column(),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("delivery_id", sa.String(length=128), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False, index=True),
        sa.UniqueConstraint("org_id", "source", "delivery_id", name="uq_webhook_deliveries"),
    )
    op.create_table(
        "outbound_webhooks",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        _org_column(),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("events", _json(), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("secret_fingerprint", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("description", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "outbound_deliveries",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        _org_column(),
        sa.Column(
            "webhook_id",
            sa.String(length=64),
            sa.ForeignKey("outbound_webhooks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event", sa.String(length=40), nullable=False),
        sa.Column("payload", _json(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending", index=True
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.BigInteger(), nullable=False, index=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )

    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    op.create_table(
        "code_embeddings",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        _org_column(),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "embedding",
            Vector(1536) if (is_postgres and Vector is not None) else sa.JSON(),
            nullable=False,
        ),
        sa.Column("created_at", sa.BigInteger(), nullable=False, index=True),
        sa.UniqueConstraint("org_id", "model", "content_hash", name="uq_code_embeddings"),
    )
    if is_postgres:
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
