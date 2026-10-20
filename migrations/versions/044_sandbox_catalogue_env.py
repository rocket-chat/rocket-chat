"""Sandbox types: environment variables set for every command of their sessions.

Revision ID: 044_sandbox_catalogue_env
Revises: 043_slack_private_dm_updates
Create Date: 2026-10-20 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "044_sandbox_catalogue_env"
down_revision: str | None = "043_slack_private_dm_updates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sandbox_catalogue",
        sa.Column(
            "env",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("sandbox_catalogue", "env")
