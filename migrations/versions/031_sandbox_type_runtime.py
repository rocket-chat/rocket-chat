"""A sandbox type can name a placement profile, resources and a workspace release delay.

Revision ID: 031_sandbox_type_runtime
Revises: 030_slack_bot_command
Create Date: 2026-10-16 18:00:00.000000

``profile`` names a placement profile the operator defined (node pool, tolerations, sizing).
``cpu_limit``, ``memory_limit`` and ``disk_size`` override the organization's sandbox resources
for the type (empty inherits). ``release_after_days`` overrides how long an idle session keeps
its workspace volume (NULL inherits ``sandboxes.workspace_release_days``).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "031_sandbox_type_runtime"
down_revision: str | None = "030_slack_bot_command"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TEXT = (("profile", 64), ("cpu_limit", 16), ("memory_limit", 16), ("disk_size", 16))


def upgrade() -> None:
    for name, length in _TEXT:
        op.add_column(
            "sandbox_catalogue",
            sa.Column(name, sa.String(length=length), nullable=False, server_default=""),
        )
    op.add_column("sandbox_catalogue", sa.Column("release_after_days", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("sandbox_catalogue", "release_after_days")
    for name, _ in reversed(_TEXT):
        op.drop_column("sandbox_catalogue", name)
