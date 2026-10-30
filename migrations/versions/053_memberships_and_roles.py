"""Memberships, invitations, the new role names, and the end of teams and policies.

Revision ID: 053_memberships_and_roles
Revises: 052_session_queued_messages
Create Date: 2026-10-29 18:00:00.000000

A person may now belong to several organizations: ``users`` is their global identity (keyed
by the identity provider's issuer and subject when they signed in through one), and
``memberships`` holds their role in each organization. Rows are filled from ``users.org_id``
and ``users.role``; ``users.org_id`` stays as the home organization for one release.

Forced RLS on ``memberships``: an organization reads its own rows, and a person reads their
own across organizations (``app.current_user_id``, for the organization switcher), but only
the organization's context may write one. ``org_invites`` is plain tenant-isolated.

Roles are renamed ``developer`` -> ``member`` and ``readonly`` -> ``viewer``; a human ``guest``
becomes ``viewer`` (it was already held to read-only), while guest API tokens keep ``guest``.

``teams``, ``policies`` and the ``team_id`` columns were never used and are dropped.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "053_memberships_and_roles"
down_revision: str | None = "052_session_queued_messages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rename_roles() -> None:
    op.execute("UPDATE users SET role = 'member' WHERE role = 'developer'")
    op.execute("UPDATE users SET role = 'viewer' WHERE role = 'readonly'")
    op.execute(
        "UPDATE users SET role = 'viewer' WHERE role = 'guest' "
        "AND NOT (COALESCE(settings::jsonb, '{}'::jsonb) ? 'api_token_guest')"
    )


def _create_memberships() -> None:
    op.create_table(
        "memberships",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
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
        sa.Column("role", sa.String(length=50), nullable=False, server_default="member"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("user_id", "org_id", name="uq_memberships_user_org"),
    )
    op.execute(
        "INSERT INTO memberships (id, user_id, org_id, role, created_at) "
        "SELECT 'mbr_' || substr(md5(id || org_id), 1, 16), id, org_id, role, created_at "
        "FROM users"
    )


def _create_invites() -> None:
    op.create_table(
        "org_invites",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False, server_default="member"),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("accepted_at", sa.BigInteger(), nullable=True),
        sa.Column("accepted_by", sa.String(length=64), nullable=True),
    )


def _secure() -> None:
    op.execute("ALTER TABLE memberships ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE memberships FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation_memberships ON memberships
            FOR ALL
            USING (
                org_id = current_setting('app.current_org_id', true)
                OR user_id = current_setting('app.current_user_id', true)
            )
            WITH CHECK (org_id = current_setting('app.current_org_id', true));
        """
    )
    op.execute("ALTER TABLE org_invites ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE org_invites FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation_org_invites ON org_invites
            FOR ALL
            USING (org_id = current_setting('app.current_org_id', true));
        """
    )


def _drop_teams_and_policies() -> None:
    op.drop_table("policies")
    op.drop_column("byok_credentials", "team_id")
    op.drop_column("users", "team_id")
    op.drop_table("teams")


def upgrade() -> None:
    bind = op.get_bind()
    postgres = bind.dialect.name == "postgresql"
    if postgres:
        _rename_roles()
    op.add_column("users", sa.Column("idp_issuer", sa.String(length=500), nullable=True))
    op.add_column("users", sa.Column("idp_subject", sa.String(length=255), nullable=True))
    op.create_index(
        "uq_users_idp_identity",
        "users",
        ["idp_issuer", "idp_subject"],
        unique=True,
        postgresql_where=sa.text("idp_subject IS NOT NULL"),
    )
    _create_memberships()
    _create_invites()
    if postgres:
        _secure()
    _drop_teams_and_policies()


def downgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("idp_group", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "team_id",
            sa.String(length=64),
            sa.ForeignKey("teams.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "byok_credentials",
        sa.Column(
            "team_id",
            sa.String(length=64),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_table(
        "policies",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("team_id", sa.String(length=64), nullable=True),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("policy_type", sa.String(length=50), nullable=False),
        sa.Column("policy_data", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation_org_invites ON org_invites;")
        op.execute("DROP POLICY IF EXISTS tenant_isolation_memberships ON memberships;")
        op.execute("UPDATE users SET role = 'developer' WHERE role = 'member'")
        op.execute("UPDATE users SET role = 'readonly' WHERE role = 'viewer'")
    op.drop_table("org_invites")
    op.drop_table("memberships")
    op.drop_index("uq_users_idp_identity", table_name="users")
    op.drop_column("users", "idp_subject")
    op.drop_column("users", "idp_issuer")
