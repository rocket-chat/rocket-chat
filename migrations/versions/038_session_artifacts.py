"""Session artifacts with Row-Level Security.

Revision ID: 038_session_artifacts
Revises: 037_agent_permission_mode
Create Date: 2026-10-18 00:30:00.000000

Persists durable session artifacts (markdown documents, specifications, diffs,
diagrams, and code deliverables) in PostgreSQL with forced Row-Level Security,
readable independently of sandbox pod lifecycles.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "038_session_artifacts"
down_revision: str | None = "037_agent_permission_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_artifacts",
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
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("artifact_type", sa.String(length=32), nullable=False, server_default="markdown"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE session_artifacts ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE session_artifacts FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_session_artifacts ON session_artifacts
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation_session_artifacts ON session_artifacts;")
    op.drop_table("session_artifacts")
