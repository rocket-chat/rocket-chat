"""Add reasoning_blocks to session_messages for durable Anthropic thinking signatures.

Revision ID: 040_msg_reasoning_blocks
Revises: 039_session_waits
Create Date: 2026-10-18 01:30:00.000000

Persists provider reasoning blocks (e.g. Anthropic thinking blocks with crypto signatures)
so session rehydration does not lose required signature pairings for tool use.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "040_msg_reasoning_blocks"
down_revision: str | None = "039_session_waits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "session_messages",
        sa.Column("reasoning_blocks", _JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("session_messages", "reasoning_blocks")
