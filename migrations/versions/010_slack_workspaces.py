"""Slack workspace -> organization binding.

Revision ID: 010_slack_workspaces
Revises: 009_session_messages
Create Date: 2026-10-10 12:00:00.000000

A Slack event only carries the workspace (team) id, so the owning organization is looked
up here before any tenant context exists - which is why this table has no row-level
security. Only administrators write it, through the API.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "010_slack_workspaces"
down_revision: str | None = "009_session_messages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "slack_workspaces",
        sa.Column("team_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("bound_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("slack_workspaces")
