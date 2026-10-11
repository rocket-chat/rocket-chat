"""Pin and archive flags for session listing ergonomics (spec section 8.1.4).

Revision ID: 004_session_flags
Revises: 003_turn_traces
Create Date: 2026-10-13 10:00:00.000000

``sessions`` already carries Row-Level Security (``ENABLE``/``FORCE`` plus the
``tenant_isolation_sessions`` policy created in migration 001 and re-asserted by
``DatabaseSessionManager.apply_rls_policies``), so adding columns needs no new policy —
the existing one already scopes every read and write by ``org_id``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004_session_flags"
down_revision: str | None = "003_turn_traces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "sessions",
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("sessions", "is_archived")
    op.drop_column("sessions", "is_pinned")
