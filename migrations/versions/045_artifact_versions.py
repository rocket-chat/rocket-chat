"""Session artifact versions with Row-Level Security.

Revision ID: 045_artifact_versions
Revises: 044_sandbox_catalogue_env
Create Date: 2026-10-20 20:00:00.000000

An artifact keeps every version it has had, so an update never loses what it replaced.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "045_artifact_versions"
down_revision: str | None = "044_sandbox_catalogue_env"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_artifact_versions",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "artifact_id",
            sa.String(length=64),
            sa.ForeignKey("session_artifacts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("session_id", sa.String(length=64), nullable=False, index=True),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("artifact_id", "version", name="uq_artifact_version"),
    )
    # Versions of artifacts that already exist: their current content is the only one known.
    op.execute(
        """
        INSERT INTO session_artifact_versions
            (id, artifact_id, session_id, org_id, version, content, created_at)
        SELECT 'artv_' || id || '_' || version, id, session_id, org_id, version, content, updated_at
        FROM session_artifacts
        """
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE session_artifact_versions ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE session_artifact_versions FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_session_artifact_versions ON session_artifact_versions
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP POLICY IF EXISTS tenant_isolation_session_artifact_versions "
            "ON session_artifact_versions;"
        )
    op.drop_table("session_artifact_versions")
