"""Add default_permission_mode column to agent_personas table.

Revision ID: 037_agent_permission_mode
Revises: 036_agent_persona_enabled
Create Date: 2026-10-17 15:00:00.000000

Allows configuring a default permission mode per agent persona.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "037_agent_permission_mode"
down_revision: str | None = "036_agent_persona_enabled"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_personas",
        sa.Column("default_permission_mode", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_personas", "default_permission_mode")
