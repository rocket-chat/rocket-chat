"""Attachment references on turn traces.

Revision ID: 021_turn_trace_attachments
Revises: 020_mcp_member_tokens
Create Date: 2026-10-11 09:00:00.000000

A turn trace records which files the user attached (id, name, type, size). The bytes stay in
the ``attachments`` table; the trace only points at them so the turn inspector can show what
the model was given.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "021_turn_trace_attachments"
down_revision: str | None = "020_mcp_member_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "turn_traces",
        sa.Column(
            "attachments",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("turn_traces", "attachments")
