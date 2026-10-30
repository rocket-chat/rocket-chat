"""Provider credentials with an identity, agent credential bindings, agents without resources.

Revision ID: 054_provider_credentials
Revises: 053_memberships_and_roles
Create Date: 2026-10-29 18:10:00.000000

``byok_credentials`` (one key per organization or member and provider) becomes
``provider_credentials``: each credential has an id, a label, an owner (``org`` or
``member``), a kind (``api_key`` today), may be the default for its provider, may be
restricted to the agents bound to it, and a member's may be shared with organization agents.
Existing rows become the default credential of their owner and provider. The table keeps its
forced RLS on ``org_id``.

Agents gain ``credential_bindings`` and ``strict_binding``. They lose ``sandbox_image``,
``cpu_limit`` and ``memory_limit``: a sandbox type from the catalogue holds those (a value
that differs from the type's is dropped, and logged by the migration). Their
``thinking_budget_tokens`` folds into the one reasoning control, ``reasoning_effort``.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "054_provider_credentials"
down_revision: str | None = "053_memberships_and_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")


def _credentials() -> None:
    op.rename_table("byok_credentials", "provider_credentials")
    op.alter_column("provider_credentials", "user_id", new_column_name="owner_user_id")
    op.alter_column("provider_credentials", "encrypted_api_key", new_column_name="encrypted_secret")
    op.add_column(
        "provider_credentials",
        sa.Column("label", sa.String(length=255), nullable=False, server_default=""),
    )
    op.add_column(
        "provider_credentials",
        sa.Column("owner_kind", sa.String(length=16), nullable=False, server_default="org"),
    )
    op.add_column(
        "provider_credentials",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="api_key"),
    )
    op.add_column(
        "provider_credentials",
        sa.Column("default_for_provider", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "provider_credentials",
        sa.Column("restricted_to_agents", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "provider_credentials",
        sa.Column(
            "shared_with_org_agents",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "provider_credentials",
        sa.Column("updated_at", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.execute(
        "UPDATE provider_credentials SET owner_kind = 'member' WHERE owner_user_id IS NOT NULL"
    )
    op.execute("UPDATE provider_credentials SET label = provider, updated_at = created_at")
    op.execute("DELETE FROM provider_credentials WHERE org_id IS NULL")
    op.alter_column("provider_credentials", "org_id", nullable=False)


def _rename_policy() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_byok ON provider_credentials;")
    op.execute(
        """
        CREATE POLICY tenant_isolation_provider_credentials ON provider_credentials
            FOR ALL
            USING (org_id = current_setting('app.current_org_id', true));
        """
    )


def _agents() -> None:
    op.add_column(
        "agent_personas",
        sa.Column(
            "credential_bindings",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "agent_personas",
        sa.Column("strict_binding", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    bind = op.get_bind()
    overridden = bind.execute(
        sa.text(
            "SELECT id, org_id FROM agent_personas WHERE COALESCE(cpu_limit, '') <> '' "
            "OR COALESCE(memory_limit, '') <> ''"
        )
    ).all()
    for agent_id, org_id in overridden:
        logger.warning(
            "Agent %s of %s set its own CPU or memory; agents now take them from their sandbox "
            "type (Settings > Organization > Sandboxes)",
            agent_id,
            org_id,
        )
    # One reasoning control: a thinking budget without an effort becomes the nearest effort.
    op.execute(
        """
        UPDATE agent_personas SET reasoning_effort = CASE
            WHEN thinking_budget_tokens <= 4096 THEN 'low'
            WHEN thinking_budget_tokens <= 16000 THEN 'medium'
            ELSE 'high' END
        WHERE thinking_budget_tokens IS NOT NULL AND COALESCE(reasoning_effort, '') = ''
        """
    )
    op.drop_column("agent_personas", "sandbox_image")
    op.drop_column("agent_personas", "cpu_limit")
    op.drop_column("agent_personas", "memory_limit")
    op.drop_column("agent_personas", "thinking_budget_tokens")


def upgrade() -> None:
    _credentials()
    if op.get_bind().dialect.name == "postgresql":
        _rename_policy()
    _agents()


def downgrade() -> None:
    op.add_column(
        "agent_personas", sa.Column("thinking_budget_tokens", sa.Integer(), nullable=True)
    )
    op.add_column(
        "agent_personas",
        sa.Column("memory_limit", sa.String(length=50), nullable=True, server_default=""),
    )
    op.add_column(
        "agent_personas",
        sa.Column("cpu_limit", sa.String(length=50), nullable=True, server_default=""),
    )
    op.add_column(
        "agent_personas",
        sa.Column(
            "sandbox_image", sa.String(length=255), nullable=True, server_default="python:3.12-slim"
        ),
    )
    op.drop_column("agent_personas", "strict_binding")
    op.drop_column("agent_personas", "credential_bindings")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP POLICY IF EXISTS tenant_isolation_provider_credentials ON provider_credentials;"
        )
    for column in (
        "updated_at",
        "shared_with_org_agents",
        "restricted_to_agents",
        "default_for_provider",
        "kind",
        "owner_kind",
        "label",
    ):
        op.drop_column("provider_credentials", column)
    op.alter_column("provider_credentials", "encrypted_secret", new_column_name="encrypted_api_key")
    op.alter_column("provider_credentials", "owner_user_id", new_column_name="user_id")
    op.rename_table("provider_credentials", "byok_credentials")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE POLICY tenant_isolation_byok ON byok_credentials
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """
        )
