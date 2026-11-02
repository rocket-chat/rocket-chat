"""Teams set defaults of their own and offer sandbox types of their own.

Revision ID: 063_team_settings_and_sandboxes
Revises: 062_team_keys_and_limits
Create Date: 2026-11-03 09:00:00.000000

``team_settings`` (forced row-level security) holds a team's defaults per domain: organization
defaults only, used in the turns of the team's agents under the member's own choice and the
organization's locks; they go with the team. ``sandbox_catalogue.team_id`` names the team that
owns a sandbox type (its members and agents only); it restricts deletion like every team object.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "063_team_settings_and_sandboxes"
down_revision: str | None = "062_team_keys_and_limits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "team_settings",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "team_id",
            sa.String(length=64),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("domain", sa.String(length=64), nullable=False),
        sa.Column("config", sa.JSON().with_variant(postgresql.JSONB(), "postgresql")),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("team_id", "domain", name="uq_team_settings_domain"),
    )
    op.add_column(
        "sandbox_catalogue",
        sa.Column(
            "team_id",
            sa.String(length=64),
            sa.ForeignKey("teams.id", ondelete="RESTRICT", name="fk_sandbox_catalogue_team_id"),
            nullable=True,
        ),
    )
    op.create_index("ix_sandbox_catalogue_team_id", "sandbox_catalogue", ["team_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE team_settings ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE team_settings FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_team_settings ON team_settings
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    # A team's type and its secrets are its own: removed, never handed to the organization.
    op.execute("DELETE FROM sandbox_catalogue WHERE team_id IS NOT NULL")
    op.drop_index("ix_sandbox_catalogue_team_id", table_name="sandbox_catalogue")
    op.drop_constraint("fk_sandbox_catalogue_team_id", "sandbox_catalogue", type_="foreignkey")
    op.drop_column("sandbox_catalogue", "team_id")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation_team_settings ON team_settings;")
    op.drop_table("team_settings")
