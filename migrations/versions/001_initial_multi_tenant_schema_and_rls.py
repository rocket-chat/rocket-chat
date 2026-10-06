"""Initial multi-tenant schema with PostgreSQL Row-Level Security (RLS).

Revision ID: 001_initial
Revises:
Create Date: 2026-10-03 09:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Organizations
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=100), unique=True, nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column(
            "settings", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
    )

    # 2. Teams
    op.create_table(
        "teams",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("idp_group", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column(
            "settings", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
    )
    op.create_index("ix_teams_org_id", "teams", ["org_id"])

    # 3. Users
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            sa.String(length=64),
            sa.ForeignKey("teams.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False, server_default="developer"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column(
            "settings", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
    )
    op.create_index("ix_users_org_id", "users", ["org_id"])
    op.create_index("ix_users_email", "users", ["email"])

    # 4. Sessions (RLS Protected)
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False, server_default="web_ui"),
        sa.Column(
            "sandbox_status", sa.String(length=50), nullable=False, server_default="non_existent"
        ),
        sa.Column("container_image", sa.String(length=255), nullable=True),
        sa.Column("git_repo", sa.String(length=255), nullable=True),
        sa.Column("git_branch", sa.String(length=255), nullable=True),
        sa.Column(
            "conversation_history",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "metadata_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_sessions_org_id", "sessions", ["org_id"])
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_org_created", "sessions", ["org_id", "created_at"])

    # 5. BYOK Credentials (RLS Protected)
    op.create_table(
        "byok_credentials",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "team_id",
            sa.String(length=64),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("key_fingerprint", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_byok_org_id", "byok_credentials", ["org_id"])

    # 6. Policies
    op.create_table(
        "policies",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            sa.String(length=64),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("policy_type", sa.String(length=50), nullable=False),
        sa.Column(
            "policy_data", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_policies_org_id", "policies", ["org_id"])

    # 7. Apply PostgreSQL Row-Level Security (RLS)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE sessions FORCE ROW LEVEL SECURITY;")
        op.execute("""
            CREATE POLICY tenant_isolation_sessions ON sessions
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
        """)
        op.execute("ALTER TABLE byok_credentials ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE byok_credentials FORCE ROW LEVEL SECURITY;")
        op.execute("""
            CREATE POLICY tenant_isolation_byok ON byok_credentials
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
        """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation_sessions ON sessions;")
        op.execute("ALTER TABLE sessions DISABLE ROW LEVEL SECURITY;")
        op.execute("DROP POLICY IF EXISTS tenant_isolation_byok ON byok_credentials;")
        op.execute("ALTER TABLE byok_credentials DISABLE ROW LEVEL SECURITY;")

    op.drop_table("policies")
    op.drop_table("byok_credentials")
    op.drop_table("sessions")
    op.drop_table("users")
    op.drop_table("teams")
    op.drop_table("organizations")
