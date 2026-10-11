"""Scheduled background missions.

Revision ID: 018_scheduled_missions
Revises: 017_github_installations
Create Date: 2026-10-10 20:00:00.000000

A mission is a prompt (optionally against a repository) that the platform runs on a cron
schedule as a normal session owned by its creator. ``next_run_at`` is the claim token: a
scheduler replica moves it forward in a single conditional UPDATE, so each firing starts
exactly one session however many replicas run. Protected by row-level security.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "018_scheduled_missions"
down_revision: str | None = "017_github_installations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduled_missions",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("cron", sa.String(length=120), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("agent_id", sa.String(length=64), nullable=True),
        sa.Column("git_repo", sa.String(length=255), nullable=True),
        sa.Column("git_branch", sa.String(length=255), nullable=True),
        sa.Column("permission_mode", sa.String(length=16), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("next_run_at", sa.BigInteger(), nullable=True, index=True),
        sa.Column("last_run_at", sa.BigInteger(), nullable=True),
        sa.Column("last_session_id", sa.String(length=64), nullable=True),
        sa.Column("last_status", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE scheduled_missions ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE scheduled_missions FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_scheduled_missions ON scheduled_missions
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP POLICY IF EXISTS tenant_isolation_scheduled_missions ON scheduled_missions;"
        )
        op.execute("ALTER TABLE scheduled_missions DISABLE ROW LEVEL SECURITY;")
    op.drop_table("scheduled_missions")
