"""Agent switches: run without a sandbox, read the agent's sessions, propose changes.

Revision ID: 068_agent_capability_switches
Revises: 067_session_files
Create Date: 2026-11-17 11:00:00.000000

Three opt-in columns on ``agent_personas``. ``sandbox_enabled`` defaults to true so every
existing agent keeps its sandbox; the two others default to false.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "068_agent_capability_switches"
down_revision: str | None = "067_session_files"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_personas",
        sa.Column("sandbox_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "agent_personas",
        sa.Column("reads_agent_sessions", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "agent_personas",
        sa.Column("proposes_changes", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("agent_personas", "proposes_changes")
    op.drop_column("agent_personas", "reads_agent_sessions")
    op.drop_column("agent_personas", "sandbox_enabled")
