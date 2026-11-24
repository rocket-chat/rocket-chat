"""External sources: repositories skills are imported from and kept in sync with.

``external_sources`` holds the connected repositories (owned like every shared object),
``external_source_links`` where each imported object comes from, and ``change_proposals``
gains the address of the pull request an approval opens for a skill linked to a source.

Revision ID: 073_external_sources
Revises: 072_mission_delayed_runs
Create Date: 2026-11-23 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "073_external_sources"
down_revision: str | None = "072_mission_delayed_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("external_sources", "external_source_links")


def _json() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(JSONB(), "postgresql")


def _org_column() -> sa.Column[str]:
    return sa.Column(
        "org_id",
        sa.String(length=64),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


def _create_sources() -> None:
    op.create_table(
        "external_sources",
        sa.Column("id", sa.String(length=64), primary_key=True),
        _org_column(),
        sa.Column("scope", sa.String(length=20), nullable=False, server_default="org", index=True),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "team_id",
            sa.String(length=64),
            sa.ForeignKey("teams.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("repository", sa.String(length=255), nullable=False, index=True),
        sa.Column("ref", sa.String(length=255), nullable=False),
        sa.Column("auth", sa.String(length=16), nullable=False, server_default="app"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("last_commit", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("last_synced_at", sa.BigInteger(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("catalogue", _json(), nullable=True),
        sa.Column("next_sync_at", sa.BigInteger(), nullable=False, server_default="0", index=True),
        sa.Column("created_by", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )


def _create_links() -> None:
    op.create_table(
        "external_source_links",
        sa.Column("id", sa.String(length=64), primary_key=True),
        _org_column(),
        sa.Column(
            "source_id",
            sa.String(length=64),
            sa.ForeignKey("external_sources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("object_id", sa.String(length=64), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("plugin", sa.String(length=120), nullable=True),
        sa.Column("commit", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("document_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("digest", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="synced"),
        sa.Column("problem", sa.Text(), nullable=False, server_default=""),
        sa.Column("files", _json(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("org_id", "kind", "object_id", name="uq_external_source_link_object"),
    )


def upgrade() -> None:
    _create_sources()
    _create_links()
    op.add_column(
        "change_proposals",
        sa.Column("change_request_url", sa.String(length=500), nullable=False, server_default=""),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    op.drop_column("change_proposals", "change_request_url")
    for table in reversed(_TABLES):
        if op.get_bind().dialect.name == "postgresql":
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table};")
        op.drop_table(table)
