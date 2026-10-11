"""Scheduled missions can report to a Slack channel.

Revision ID: 024_mission_slack_channel
Revises: 023_sandbox_processes
Create Date: 2026-10-11 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "024_mission_slack_channel"
down_revision: str | None = "023_sandbox_processes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scheduled_missions", sa.Column("slack_channel", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("scheduled_missions", "slack_channel")
