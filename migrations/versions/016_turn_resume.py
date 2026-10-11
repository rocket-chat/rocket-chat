"""Automatic resume of interrupted turns.

Revision ID: 016_turn_resume
Revises: 015_local_accounts
Create Date: 2026-10-10 18:00:00.000000

A turn whose replica died is marked ``interrupted``. ``resumed_by`` is the atomic claim that
lets exactly one replica resume it; ``resume_attempt`` counts how many times a task has been
resumed so a turn that keeps killing its process cannot loop forever.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "016_turn_resume"
down_revision: str | None = "015_local_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "turn_traces",
        sa.Column("resume_attempt", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("turn_traces", sa.Column("resumed_by", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("turn_traces", "resumed_by")
    op.drop_column("turn_traces", "resume_attempt")
