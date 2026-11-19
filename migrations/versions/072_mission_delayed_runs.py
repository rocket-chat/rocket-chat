"""A mission's event trigger may wait before its run starts; the waiting runs are kept here.

Revision ID: 072_mission_delayed_runs
Revises: 071_mission_repos_writes
Create Date: 2026-11-18 16:00:00.000000

A trigger's ``delay_minutes`` lives in the mission's ``triggers`` JSON and needs no column. Each
event a delayed trigger takes is stored in ``mission_delayed_runs`` until it is due, then the
scheduler claims it by deleting the row and starts the run.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "072_mission_delayed_runs"
down_revision: str | None = "071_mission_repos_writes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "mission_delayed_runs"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "mission_id",
            sa.String(length=64),
            sa.ForeignKey("missions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("event", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.Column("fallback_owner", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("due_at", sa.BigInteger(), nullable=False, index=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
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
    op.drop_table(_TABLE)
