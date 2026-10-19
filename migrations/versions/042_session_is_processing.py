"""Track whether a session has an active turn so the sidebar can show a loader.

Revision ID: 042_session_is_processing
Revises: 041_backfill_msg_timestamps
Create Date: 2026-10-19 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "042_session_is_processing"
down_revision: str | None = "041_backfill_msg_timestamps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("is_processing", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("sessions", "is_processing")
