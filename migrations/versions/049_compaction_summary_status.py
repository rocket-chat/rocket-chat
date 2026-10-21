"""Record how each compaction's summary was made.

Revision ID: 049_compaction_summary_status
Revises: 048_pinned_tool_loading
Create Date: 2026-10-21 18:00:00.000000

A compaction's summary is the only trace the agent keeps of the steps it replaces. It is now
checked: ``model`` when the model's first answer was usable, ``model_retry`` when it took a second
ask, ``digest`` when both failed and the summary was rebuilt from the tool history. Checkpoints
written before this revision are ``legacy``: their summary was stored unchecked, and may be empty.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "049_compaction_summary_status"
down_revision: str | None = "048_pinned_tool_loading"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "session_compactions",
        sa.Column("summary_status", sa.String(16), nullable=False, server_default="legacy"),
    )


def downgrade() -> None:
    op.drop_column("session_compactions", "summary_status")
