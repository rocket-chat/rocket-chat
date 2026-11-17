"""Which agents answered in which sessions, for agents that read their other sessions.

Revision ID: 069_session_agents
Revises: 068_agent_capability_switches
Create Date: 2026-11-17 12:00:00.000000

``session_agents`` has one row per (session, agent) with the first and last time the agent
answered there. It is backfilled from each session's selected agent (``metadata_json``); the
turn runner keeps it current.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "069_session_agents"
down_revision: str | None = "068_agent_capability_switches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "session_agents"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "session_id",
            sa.String(length=64),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("first_at", sa.BigInteger(), nullable=False),
        sa.Column("last_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("session_id", "agent_id"),
    )
    op.create_index("ix_session_agents_agent_last", _TABLE, ["agent_id", "last_at"])
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        f"""
        INSERT INTO {_TABLE} (session_id, agent_id, org_id, first_at, last_at)
        SELECT id, metadata_json->>'agent_id', org_id, created_at, updated_at
          FROM sessions
         WHERE COALESCE(metadata_json->>'agent_id', '') <> ''
        ON CONFLICT DO NOTHING;
        """
    )
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation_{_TABLE} ON {_TABLE}
            FOR ALL
            USING (org_id = current_setting('app.current_org_id', true));
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{_TABLE} ON {_TABLE};")
    op.drop_index("ix_session_agents_agent_last", table_name=_TABLE)
    op.drop_table(_TABLE)
