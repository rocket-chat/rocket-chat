"""Session templates / playbooks.

Revision ID: 019_session_templates
Revises: 018_scheduled_missions
Create Date: 2026-10-10 21:00:00.000000

A template is a reusable way to start a session: the opening prompt, the agent, the repository
and the supervision level. ``user_id`` NULL means organization-wide (administrator-managed);
otherwise it is personal. Protected by row-level security.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "019_session_templates"
down_revision: str | None = "018_scheduled_missions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_templates",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=True),
        sa.Column("git_repo", sa.String(length=255), nullable=True),
        sa.Column("git_branch", sa.String(length=255), nullable=True),
        sa.Column("permission_mode", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE session_templates ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE session_templates FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_session_templates ON session_templates
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation_session_templates ON session_templates;")
        op.execute("ALTER TABLE session_templates DISABLE ROW LEVEL SECURITY;")
    op.drop_table("session_templates")
