"""Every agent sends the core tools and the ones it keeps active: the loading modes go.

Revision ID: 050_core_tool_set
Revises: 049_compaction_summary_status
Create Date: 2026-10-21 19:00:00.000000

The four modes (all, on_demand, pinned, auto) and the organization's ``general.tool_loading`` /
``general.tool_loading_threshold`` are replaced by one rule: the core tools (``CORE_TOOLS``) and
an agent's ``active_tools`` are sent with every step, everything else is loaded on demand. An
agent's pins are kept as its active tools; the mode column and the two settings are removed. The
downgrade restores the column empty (agents then inherit the organization's default).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "050_core_tool_set"
down_revision: str | None = "049_compaction_summary_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("agent_personas", "tool_loading")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            UPDATE org_settings
            SET config = config::jsonb - 'tool_loading' - 'tool_loading_threshold'
            WHERE domain = 'general'
              AND (config::jsonb ? 'tool_loading' OR config::jsonb ? 'tool_loading_threshold');
            """
        )


def downgrade() -> None:
    op.add_column("agent_personas", sa.Column("tool_loading", sa.String(length=20), nullable=True))
