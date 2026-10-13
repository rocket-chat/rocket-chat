"""Organization overrides of the built-in agents.

Revision ID: 027_builtin_persona_overrides
Revises: 026_github_watches
Create Date: 2026-10-13 22:00:00.000000

An administrator can edit a built-in agent for the whole organization. The edit is stored as a
normal ``agent_personas`` row (same table, same row-level security) that names the built-in it
replaces; deleting the row restores the shipped definition.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "027_builtin_persona_overrides"
down_revision: str | None = "026_github_watches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_personas", sa.Column("overrides_builtin", sa.String(length=64), nullable=True)
    )
    op.create_index(
        "uq_agent_personas_org_override",
        "agent_personas",
        ["org_id", "overrides_builtin"],
        unique=True,
        postgresql_where=sa.text("overrides_builtin IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_personas_org_override", table_name="agent_personas")
    op.drop_column("agent_personas", "overrides_builtin")
