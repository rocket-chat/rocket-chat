"""Agents choose which tools every step carries and which load on demand.

Revision ID: 028_agent_tool_loading
Revises: 027_builtin_persona_overrides
Create Date: 2026-10-15 15:00:00.000000

``tool_loading`` (all / on_demand / auto) overrides the organization's ``general.tool_loading``;
NULL inherits it. ``active_tools`` lists the tools (names or patterns) always loaded.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "028_agent_tool_loading"
down_revision: str | None = "027_builtin_persona_overrides"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_personas", sa.Column("tool_loading", sa.String(length=20), nullable=True))
    op.add_column(
        "agent_personas",
        sa.Column(
            "active_tools",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_personas", "active_tools")
    op.drop_column("agent_personas", "tool_loading")
