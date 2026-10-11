"""The question a turn is waiting on, kept with the turn (survives a restart).

Revision ID: 022_turn_trace_pending_decision
Revises: 021_turn_trace_attachments
Create Date: 2026-10-11 12:00:00.000000

A turn that asks the person a question pauses until they answer. The pending question used to
exist only in the replica's in-memory event history, so restarting the API made it vanish and
the turn could not be continued. It is now stored on the turn's trace row.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "022_turn_trace_pending_decision"
down_revision: str | None = "021_turn_trace_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "turn_traces",
        sa.Column(
            "pending_decision",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("turn_traces", "pending_decision")
