"""Subagents v2: typed role definitions, structured reports, supervised background runs.

Revision ID: 057_subagents_v2
Revises: 056_settings_merge
Create Date: 2026-10-29 12:00:00.000000

``subagent_configs`` gains what a role is (tier, isolation, budgets, inheritance, output
schema); ``subagent_transcripts`` keeps the child's whole structured report and the model it
ran on; ``subagent_runs`` holds background children as supervised tasks with a lease, under
forced row-level security on ``org_id``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "057_subagents_v2"
down_revision: str | None = "056_settings_merge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _role_columns() -> None:
    with op.batch_alter_table("subagent_configs") as table:
        table.add_column(sa.Column("tier", sa.String(16), nullable=False, server_default="default"))
        table.add_column(sa.Column("disallowed_tools", _json(), nullable=True))
        table.add_column(sa.Column("reasoning_effort", sa.String(16), nullable=True))
        table.add_column(
            sa.Column("max_cost_usd", sa.Float(), nullable=False, server_default="3.0")
        )
        table.add_column(
            sa.Column("max_wall_seconds", sa.Integer(), nullable=False, server_default="0")
        )
        table.add_column(
            sa.Column("isolation", sa.String(16), nullable=False, server_default="none")
        )
        table.add_column(
            sa.Column("can_ask_user", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        table.add_column(sa.Column("inherit", _json(), nullable=True))
        table.add_column(sa.Column("output_schema", _json(), nullable=True))


def upgrade() -> None:
    _role_columns()
    with op.batch_alter_table("subagent_transcripts") as table:
        table.add_column(sa.Column("report", _json(), nullable=True))
        table.add_column(sa.Column("model", sa.String(255), nullable=False, server_default=""))
    op.create_table(
        "subagent_runs",
        sa.Column("id", sa.String(64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "session_id",
            sa.String(64),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("subagent_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("background", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("parent_tool_call_id", sa.String(128), nullable=True),
        sa.Column("lease_owner", sa.String(128), nullable=False, server_default=""),
        sa.Column("lease_expires_at", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result", _json(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("session_id", "subagent_id", name="uq_subagent_runs_child"),
    )
    op.create_index(
        "ix_subagent_runs_active_lease", "subagent_runs", ["status", "lease_expires_at"]
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE subagent_runs ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE subagent_runs FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_subagent_runs ON subagent_runs
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation_subagent_runs ON subagent_runs;")
    op.drop_index("ix_subagent_runs_active_lease", table_name="subagent_runs")
    op.drop_table("subagent_runs")
    with op.batch_alter_table("subagent_transcripts") as table:
        table.drop_column("model")
        table.drop_column("report")
    with op.batch_alter_table("subagent_configs") as table:
        for column in (
            "output_schema", "inherit", "can_ask_user", "isolation", "max_wall_seconds",
            "max_cost_usd", "reasoning_effort", "disallowed_tools", "tier",
        ):  # fmt: skip
            table.drop_column(column)
