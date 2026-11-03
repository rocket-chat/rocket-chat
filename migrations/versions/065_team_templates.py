"""Session templates are owned like every shared object: a member, a team or the organization.

Revision ID: 065_team_templates
Revises: 064_unified_missions
Create Date: 2026-11-03 15:00:00.000000

``session_templates`` gains ``scope`` (backfilled: ``org`` where it had no member, else
``user``) and ``team_id`` (restricting deletion, like every team object).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "065_team_templates"
down_revision: str | None = "064_unified_missions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "session_templates",
        sa.Column("scope", sa.String(length=20), nullable=False, server_default="user"),
    )
    op.add_column(
        "session_templates",
        sa.Column(
            "team_id",
            sa.String(length=64),
            sa.ForeignKey("teams.id", ondelete="RESTRICT", name="fk_session_templates_team_id"),
            nullable=True,
        ),
    )
    op.create_index("ix_session_templates_team_id", "session_templates", ["team_id"])
    op.execute("UPDATE session_templates SET scope = 'org' WHERE user_id IS NULL")


def downgrade() -> None:
    # Kept, never widened: a team's template becomes nobody's personal one rather than the
    # organization's.
    op.execute("UPDATE session_templates SET user_id = '' WHERE scope = 'team'")
    op.drop_index("ix_session_templates_team_id", table_name="session_templates")
    op.drop_constraint("fk_session_templates_team_id", "session_templates", type_="foreignkey")
    op.drop_column("session_templates", "team_id")
    op.drop_column("session_templates", "scope")
