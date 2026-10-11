"""Per-member OAuth tokens for organization MCP servers.

Revision ID: 020_mcp_member_tokens
Revises: 019_session_templates
Create Date: 2026-10-10 22:00:00.000000

An organization server in "member" OAuth mode is registered once by an administrator, but each
member connects their *own* account to it. Their tokens live here (encrypted, one row per
member and server), never on the shared server row, so one member's token can never be used for
another member's turn. Protected by row-level security.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "020_mcp_member_tokens"
down_revision: str | None = "019_session_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_member_tokens",
        sa.Column(
            "server_id",
            sa.String(length=64),
            sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "tokens",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE mcp_member_tokens ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE mcp_member_tokens FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_mcp_member_tokens ON mcp_member_tokens
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation_mcp_member_tokens ON mcp_member_tokens;")
        op.execute("ALTER TABLE mcp_member_tokens DISABLE ROW LEVEL SECURITY;")
    op.drop_table("mcp_member_tokens")
