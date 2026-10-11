"""Turn leases: which replica runs a turn and when it last proved it is alive.

Revision ID: 012_turn_leases
Revises: 011_session_event_counters
Create Date: 2026-10-10 16:00:00.000000

With several API replicas, "every running turn belongs to a dead process" is no longer true
at startup. A replica stamps its turns with its identity and a heartbeat; only turns whose
heartbeat has gone stale are recovered.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "012_turn_leases"
down_revision: str | None = "011_session_event_counters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("turn_traces", sa.Column("owner_instance", sa.String(length=64), nullable=True))
    op.add_column("turn_traces", sa.Column("heartbeat_at", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("turn_traces", "heartbeat_at")
    op.drop_column("turn_traces", "owner_instance")
