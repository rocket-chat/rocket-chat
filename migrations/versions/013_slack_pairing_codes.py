"""Shared Slack pairing codes.

Revision ID: 013_slack_pairing_codes
Revises: 012_turn_leases
Create Date: 2026-10-10 17:00:00.000000

``/rocket-pair`` runs on whichever replica holds the Slack connection; the member finishes the
pairing from their browser on any replica, so the one-time code lives in the database (as a
hash). Like ``slack_workspaces`` it has no row-level security: it is read before the claimant's
organization is known to the Slack side.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "013_slack_pairing_codes"
down_revision: str | None = "012_turn_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "slack_pairing_codes",
        sa.Column("code_hash", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("slack_user_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("team_id", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table("slack_pairing_codes")
