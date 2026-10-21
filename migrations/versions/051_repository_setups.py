"""Repository setups an organization defines, with Row-Level Security.

Revision ID: 051_repository_setups
Revises: 050_core_tool_set
Create Date: 2026-10-21 20:00:00.000000

How to prepare a folder of a repository (a service of a monorepo, say) before the agent builds or
tests there: commands the platform runs once per workspace, before the first command in a
matching folder, instead of the agent working out (or skipping) the steps from prose.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "051_repository_setups"
down_revision: str | None = "050_core_tool_set"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "repository_setups",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("repository", sa.String(length=255), nullable=False),
        sa.Column("folder", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="900"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE repository_setups ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE repository_setups FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_repository_setups ON repository_setups
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation_repository_setups ON repository_setups;")
    op.drop_table("repository_setups")
