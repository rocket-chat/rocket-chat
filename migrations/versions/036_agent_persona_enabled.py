"""Add enabled column to agent_personas table.

Revision ID: 036_agent_persona_enabled
Revises: 035_subagent_transcripts
Create Date: 2026-10-17 14:00:00.000000

Allows organization administrators to disable or hide built-in and custom agent personas.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "036_agent_persona_enabled"
down_revision: str | None = "035_subagent_transcripts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_personas",
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("agent_personas", "enabled")
