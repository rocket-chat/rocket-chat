"""Teams own provider keys and spend limits, and organization agents can be granted to a team.

Revision ID: 062_team_keys_and_limits
Revises: 061_teams
Create Date: 2026-11-02 18:00:00.000000

``provider_credentials.team_id`` names the team of an ``owner_kind="team"`` key (its agents'
turns use it before the organization's); it restricts deletion like every team object.
``spend_limits.team_id`` names the team that owns a limit (its administrators manage it); the
limit goes with its team, and ``team`` becomes a limit subject. ``team`` also becomes a
principal of ``agent_grants``. Every table keeps its forced row-level security.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "062_team_keys_and_limits"
down_revision: str | None = "061_teams"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SUBJECTS = "'org', 'member', 'agent', 'agent_member', 'slack_workspace'"
_PRINCIPALS = "'everyone', 'member', 'role', 'guests'"


def _replace_check(table: str, name: str, condition: str) -> None:
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, condition)


def upgrade() -> None:
    _replace_check(
        "spend_limits", "ck_spend_limits_subject", f"subject_kind IN ({_SUBJECTS}, 'team')"
    )
    _replace_check(
        "agent_grants", "ck_agent_grants_principal", f"principal_kind IN ({_PRINCIPALS}, 'team')"
    )
    op.add_column(
        "provider_credentials",
        sa.Column(
            "team_id",
            sa.String(length=64),
            sa.ForeignKey("teams.id", ondelete="RESTRICT", name="fk_provider_credentials_team_id"),
            nullable=True,
        ),
    )
    op.create_index("ix_provider_credentials_team_id", "provider_credentials", ["team_id"])
    op.add_column(
        "spend_limits",
        sa.Column(
            "team_id",
            sa.String(length=64),
            sa.ForeignKey("teams.id", ondelete="CASCADE", name="fk_spend_limits_team_id"),
            nullable=True,
        ),
    )
    op.create_index("ix_spend_limits_team_id", "spend_limits", ["team_id"])


def downgrade() -> None:
    # A team's key has no owner without its team: it is removed rather than handed to the
    # organization. Team-subject limits mean nothing without teams either.
    op.execute("DELETE FROM provider_credentials WHERE owner_kind = 'team'")
    op.execute("DELETE FROM spend_limits WHERE subject_kind = 'team' OR team_id IS NOT NULL")
    op.execute("DELETE FROM agent_grants WHERE principal_kind = 'team'")
    _replace_check("spend_limits", "ck_spend_limits_subject", f"subject_kind IN ({_SUBJECTS})")
    _replace_check(
        "agent_grants", "ck_agent_grants_principal", f"principal_kind IN ({_PRINCIPALS})"
    )
    op.drop_index("ix_spend_limits_team_id", table_name="spend_limits")
    op.drop_constraint("fk_spend_limits_team_id", "spend_limits", type_="foreignkey")
    op.drop_column("spend_limits", "team_id")
    op.drop_index("ix_provider_credentials_team_id", table_name="provider_credentials")
    op.drop_constraint(
        "fk_provider_credentials_team_id", "provider_credentials", type_="foreignkey"
    )
    op.drop_column("provider_credentials", "team_id")
