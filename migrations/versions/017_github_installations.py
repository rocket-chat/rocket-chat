"""GitHub App installations mapped to organizations.

Revision ID: 017_github_installations
Revises: 016_turn_resume
Create Date: 2026-10-10 19:00:00.000000

One GitHub App serves the whole deployment, but an installation belongs to one GitHub
account's repositories. Without a mapping, any organization could obtain a token for another
organization's installation. Like ``slack_workspaces`` the table has no row-level security: the
installation id arrives on a GitHub redirect before any tenant is known.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "017_github_installations"
down_revision: str | None = "016_turn_resume"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "github_installations",
        sa.Column("installation_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("account_login", sa.String(length=255), nullable=False),
        sa.Column("bound_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("github_installations")
