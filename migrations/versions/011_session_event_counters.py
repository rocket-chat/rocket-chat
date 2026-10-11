"""Shared per-session event sequence for multi-replica streaming.

Revision ID: 011_session_event_counters
Revises: 010_slack_workspaces
Create Date: 2026-10-10 15:00:00.000000

Every API replica numbers a session's events from this counter so a client can resume a
stream on a different replica than the one that started it. The table carries no tenant data
(an opaque session id and a counter), so it has no row-level security.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "011_session_event_counters"
down_revision: str | None = "010_slack_workspaces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_event_counters",
        sa.Column("session_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("last_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.BigInteger(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("session_event_counters")
