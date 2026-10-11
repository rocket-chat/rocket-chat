"""Local (password) accounts for self-hosted installs without an identity provider.

Revision ID: 015_local_accounts
Revises: 014_usage_events
Create Date: 2026-10-10 19:00:00.000000

Adds the credential columns to ``users``: an argon2id hash (never the password), a
must-change flag for administrator-issued temporary passwords, and the failed-attempt counter
and lock-out deadline that make online guessing impractical. SSO members have a null hash and
cannot log in with a password.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "015_local_accounts"
down_revision: str | None = "014_usage_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("password_updated_at", sa.BigInteger(), nullable=True))
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "users", sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("users", sa.Column("locked_until", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    for column in (
        "locked_until",
        "failed_login_count",
        "must_change_password",
        "password_updated_at",
        "password_hash",
    ):
        op.drop_column("users", column)
