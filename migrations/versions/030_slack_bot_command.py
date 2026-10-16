"""A Slack bot can choose its slash command.

Revision ID: 030_slack_bot_command
Revises: 029_slack_bots
Create Date: 2026-10-16 12:00:00.000000

``slash_command`` overrides the command named after the bot's handle; NULL keeps that name.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "030_slack_bot_command"
down_revision: str | None = "029_slack_bots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("slack_bots", sa.Column("slash_command", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("slack_bots", "slash_command")
