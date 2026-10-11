"""Attachments table with PostgreSQL Row-Level Security (spec section 8.2.1).

Revision ID: 005_attachments
Revises: 004_session_flags
Create Date: 2026-10-13 10:05:00.000000

Stores one row per uploaded file. ``sha256`` plus a unique ``(session_id, sha256)`` index
is the dedupe point so the same bytes in one session resolve to one stored blob.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "005_attachments"
down_revision: str | None = "004_session_flags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attachments",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "session_id",
            sa.String(length=64),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("mime", sa.String(length=255), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False, index=True),
        sa.Column("workspace_path", sa.String(length=1024), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "uq_attachments_session_sha256",
        "attachments",
        ["session_id", "sha256"],
        unique=True,
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE attachments ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE attachments FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_attachments ON attachments
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation_attachments ON attachments;")
        op.execute("ALTER TABLE attachments DISABLE ROW LEVEL SECURITY;")

    op.drop_index("uq_attachments_session_sha256", table_name="attachments")
    op.drop_table("attachments")
