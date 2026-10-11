"""Per-organization sandbox catalogue.

Revision ID: 025_sandbox_catalogue
Revises: 024_mission_slack_channel
Create Date: 2026-10-11 18:00:00.000000

The images an organization offers for sessions: a name, the image, what it can do (capability
tags such as ``browser``) and which one is the default. It replaces the free-text
``sandboxes.default_image`` / ``allowed_images`` pair, which now only seed the catalogue the
first time an organization uses it. Protected by row-level security.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "025_sandbox_catalogue"
down_revision: str | None = "024_mission_slack_channel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sandbox_catalogue",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("image", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False, server_default=""),
        sa.Column(
            "capabilities",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("org_id", "name", name="uq_sandbox_catalogue_org_name"),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE sandbox_catalogue ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE sandbox_catalogue FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_sandbox_catalogue ON sandbox_catalogue
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation_sandbox_catalogue ON sandbox_catalogue;")
        op.execute("ALTER TABLE sandbox_catalogue DISABLE ROW LEVEL SECURITY;")
    op.drop_table("sandbox_catalogue")
