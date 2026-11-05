"""Teams: groups of an organization's members that own agents, skills, MCP servers and knowledge.

Revision ID: 061_teams
Revises: 060_chat_thread_link_access
Create Date: 2026-11-02 10:00:00.000000

``teams`` and ``team_memberships`` (role ``admin`` or ``member``) are each organization's own,
under forced Row-Level Security. Agents, skills, MCP servers and knowledge bases gain a
``team_id`` for their new ``team`` scope; it restricts deletion, so a team that still owns
objects cannot be removed. Organization isolation is unchanged: teams only decide who inside
an organization sees and changes what.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "061_teams"
down_revision: str | None = "060_chat_thread_link_access"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("teams", "team_memberships")
_OWNED = ("agent_personas", "custom_skills", "mcp_servers", "knowledge_bases")


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
        "teams",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        _org_column(),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("org_id", "name", name="uq_teams_org_name"),
    )
    op.create_table(
        "team_memberships",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        _org_column(),
        sa.Column(
            "team_id",
            sa.String(length=64),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
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
        sa.Column("role", sa.String(length=16), nullable=False, server_default="member"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_memberships"),
    )
    for table in _OWNED:
        op.add_column(
            table,
            sa.Column(
                "team_id",
                sa.String(length=64),
                sa.ForeignKey("teams.id", ondelete="RESTRICT", name=f"fk_{table}_team_id"),
                nullable=True,
            ),
        )
        op.create_index(f"ix_{table}_team_id", table, ["team_id"])
    if op.get_bind().dialect.name == "postgresql":
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
    for table in _OWNED:
        # Kept, never widened: a personal row with no member is visible to nobody, where org
        # scope would hand a team's object to everyone.
        op.execute(f"UPDATE {table} SET scope = 'user' WHERE scope = 'team'")
        op.drop_index(f"ix_{table}_team_id", table_name=table)
        op.drop_constraint(f"fk_{table}_team_id", table, type_="foreignkey")
        op.drop_column(table, "team_id")
    for table in reversed(_TABLES):
        if bind.dialect.name == "postgresql":
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table};")
        op.drop_table(table)
