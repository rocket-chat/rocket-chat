"""Sessions that follow a GitHub pull request.

Revision ID: 026_github_watches
Revises: 025_sandbox_catalogue
Create Date: 2026-10-12 09:00:00.000000

A watch links one session to a repository's pull request (or branch) so the events GitHub
sends (CI finished, a review, the pull request closed) reach that session instead of starting a
new one. Protected by row-level security; deleting a session deletes its watches.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "026_github_watches"
down_revision: str | None = "025_sandbox_catalogue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "github_watches",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "session_id",
            sa.String(length=64),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("repo", sa.String(length=200), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column("wake", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("wakes_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column(
            "pending_events",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
    )
    op.create_index("ix_github_watches_org_repo", "github_watches", ["org_id", "repo"])
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE github_watches ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE github_watches FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_github_watches ON github_watches
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation_github_watches ON github_watches;")
        op.execute("ALTER TABLE github_watches DISABLE ROW LEVEL SECURITY;")
    op.drop_table("github_watches")
