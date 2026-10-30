"""Spend limits with atomic reservation, agent access grants, and usage attribution.

Revision ID: 055_spend_and_grants
Revises: 054_provider_credentials
Create Date: 2026-10-29 18:20:00.000000

* ``spend_limits``: a limit on a subject (organization, member or each member, agent, agent for
  a member or for each, Slack workspace) over a period (day, week, month, rolling 24 h or 7 d,
  session, turn), which blocks or warns.
* ``spend_counters``: spent and reserved dollars per limit, subject and window (one row per
  calendar window, per hour for a rolling one, per session or turn). A call reserves its
  estimate with a conditional ``UPDATE`` before it is made.
* ``spend_reservations``: the estimates not settled yet, so a sweeper can release those of a
  call that crashed.
* ``agent_grants``: who may use an organization agent (no rows: every member, not guests).
* ``usage_events`` gains the agent, credential, turn, subagent, call role and requester of each
  call, indexed by organization, agent and time. Rows before it read as "unattributed".

Every new table has forced RLS on ``org_id``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "055_spend_and_grants"
down_revision: str | None = "054_provider_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("spend_limits", "spend_counters", "spend_reservations", "agent_grants")
_ATTRIBUTION = (
    ("agent_id", 64),
    ("credential_id", 64),
    ("turn_id", 64),
    ("subagent_id", 64),
    ("call_role", 64),
    ("requester_user_id", 64),
)


def _org_column() -> sa.Column[str]:
    return sa.Column(
        "org_id",
        sa.String(length=64),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


def _spend_tables() -> None:
    op.create_table(
        "spend_limits",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        _org_column(),
        sa.Column("subject_kind", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=True),
        sa.Column("member_id", sa.String(length=64), nullable=True),
        sa.Column("period", sa.String(length=16), nullable=False),
        sa.Column("amount_usd", sa.Float(), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False, server_default="block"),
        sa.Column("warn_at_percent", sa.Integer(), nullable=False, server_default="80"),
        sa.Column("is_ceiling", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "subject_kind IN ('org', 'member', 'agent', 'agent_member', 'slack_workspace')",
            name="ck_spend_limits_subject",
        ),
        sa.CheckConstraint(
            "period IN ('day', 'week', 'month', 'rolling_24h', 'rolling_7d', 'session', 'turn')",
            name="ck_spend_limits_period",
        ),
        sa.CheckConstraint("mode IN ('warn', 'block')", name="ck_spend_limits_mode"),
    )
    op.create_table(
        "spend_counters",
        sa.Column(
            "limit_id",
            sa.String(length=64),
            sa.ForeignKey("spend_limits.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("subject_key", sa.String(length=200), primary_key=True),
        sa.Column("window_start", sa.BigInteger(), primary_key=True),
        _org_column(),
        sa.Column("spent_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reserved_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_table(
        "spend_reservations",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        _org_column(),
        sa.Column("amount_usd", sa.Float(), nullable=False),
        sa.Column(
            "counters",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.BigInteger(), nullable=False, index=True),
    )


def _grants() -> None:
    op.create_table(
        "agent_grants",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        _org_column(),
        sa.Column("agent_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("principal_kind", sa.String(length=16), nullable=False),
        sa.Column("principal_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            "agent_id", "principal_kind", "principal_id", name="uq_agent_grants_principal"
        ),
        sa.CheckConstraint(
            "principal_kind IN ('everyone', 'member', 'role', 'guests')",
            name="ck_agent_grants_principal",
        ),
    )


def _attribution() -> None:
    for name, length in _ATTRIBUTION:
        op.add_column("usage_events", sa.Column(name, sa.String(length=length), nullable=True))
    op.create_index(
        "ix_usage_events_org_agent_created", "usage_events", ["org_id", "agent_id", "created_at"]
    )


def upgrade() -> None:
    _spend_tables()
    _grants()
    _attribution()
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
    if op.get_bind().dialect.name == "postgresql":
        for table in _TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table};")
    op.drop_index("ix_usage_events_org_agent_created", table_name="usage_events")
    for name, _ in reversed(_ATTRIBUTION):
        op.drop_column("usage_events", name)
    op.drop_table("agent_grants")
    op.drop_table("spend_reservations")
    op.drop_table("spend_counters")
    op.drop_table("spend_limits")
