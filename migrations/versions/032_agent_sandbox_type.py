"""An agent can name the sandbox type its sessions start in.

Revision ID: 032_agent_sandbox_type
Revises: 031_sandbox_type_runtime
Create Date: 2026-10-16 19:00:00.000000

``sandbox_id`` is a ``sandbox_catalogue`` entry of the agent's organization, used when the
session's creator picks none; empty uses the organization's default. It is not a foreign key:
deleting a type must not delete or block the agents naming it, which then use the default.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "032_agent_sandbox_type"
down_revision: str | None = "031_sandbox_type_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_personas",
        sa.Column("sandbox_id", sa.String(length=64), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("agent_personas", "sandbox_id")
