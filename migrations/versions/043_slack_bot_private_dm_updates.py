"""Slack bots: send a private request's questions, approvals, errors and result by DM.

Revision ID: 043_slack_private_dm_updates
Revises: 042_session_is_processing
Create Date: 2026-10-20 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "043_slack_private_dm_updates"
down_revision: str | None = "042_session_is_processing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "slack_bots",
        sa.Column("private_dm_updates", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("slack_bots", "private_dm_updates")
