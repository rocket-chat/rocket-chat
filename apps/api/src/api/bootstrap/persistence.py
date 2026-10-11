"""Open the session store: PostgreSQL when configured, in-memory otherwise."""

import contextlib
import logging
import os
from dataclasses import dataclass

from api.auth.identity import dev_identity_enabled, is_production
from api.auth.local import bootstrap_initial_admin
from api.bootstrap.runtime import cluster_bus_enabled
from api.db.models import OrganizationModel, UserModel
from api.db.schema import assert_schema_current
from api.db.session import DatabaseSessionManager
from api.db.store import PostgresSessionStore
from api.services.turn_trace import LEASE_TTL_SECONDS, recover_interrupted_turns
from api.store import InMemorySessionStore
from specifications.interfaces.session import SessionStoreProtocol

logger = logging.getLogger(__name__)

DEFAULT_ORG_ID = "default_org"
DEV_USER_ID = "dev_user"


@dataclass(frozen=True)
class Persistence:
    """The session store and, when a database backs it, its connection manager."""

    session_store: SessionStoreProtocol
    db_manager: DatabaseSessionManager | None


async def open_persistence(database_url: str | None) -> Persistence:
    """Connect to the database, or fall back to memory outside production."""
    if not database_url:
        logger.info("DATABASE_URL not set; using InMemorySessionStore.")
        return Persistence(InMemorySessionStore(), None)

    manager = DatabaseSessionManager(database_url)
    try:
        logger.info(
            "Initializing persistent PostgresSessionStore with %s...", database_url.split("@")[-1]
        )
        await _prepare_schema(manager)
        await _seed_default_identity(manager)
        await bootstrap_initial_admin(manager)
        store = PostgresSessionStore(manager)
        logger.info("PostgresSessionStore initialized successfully.")
        await _recover_interrupted_turns(os.getenv("ADMIN_DATABASE_URL") or database_url)
    except Exception as db_err:
        if is_production():
            # Falling back to memory in production would silently lose every session on
            # restart and split state across replicas: refuse to start instead.
            raise
        logger.warning(
            "Failed connecting to PostgreSQL (%s); falling back to InMemorySessionStore.", db_err
        )
        with contextlib.suppress(Exception):
            await manager.close()
        return Persistence(InMemorySessionStore(), None)
    return Persistence(store, manager)


async def _prepare_schema(manager: DatabaseSessionManager) -> None:
    auto_create = os.getenv("ROCKET_AUTO_CREATE_SCHEMA", "").lower() in ("1", "true", "yes")
    if auto_create and not is_production():
        # Development/test convenience only. Real deployments migrate with Alembic.
        await manager.create_tables()
    else:
        await assert_schema_current(manager)


async def _seed_default_identity(manager: DatabaseSessionManager) -> None:
    """Ensure the default organization (and the dev identity, when enabled) exist for foreign keys."""
    async with manager.session() as session:
        if await session.get(OrganizationModel, DEFAULT_ORG_ID) is None:
            session.add(
                OrganizationModel(
                    id=DEFAULT_ORG_ID, name="Default Organization", slug="default-org"
                )
            )
            await session.flush()
        if await session.get(UserModel, DEV_USER_ID) is None and dev_identity_enabled():
            session.add(
                UserModel(
                    id=DEV_USER_ID,
                    org_id=DEFAULT_ORG_ID,
                    email="dev@rocket.chat",
                    name="Developer",
                )
            )
            await session.flush()


async def _recover_interrupted_turns(recovery_url: str) -> None:
    """Surface turns whose process died mid-flight as ``interrupted``.

    The cockpit can then offer a retry instead of silently losing the turn. RLS is FORCEd
    and keyed on the tenant, so the cross-tenant sweep needs the schema-owner connection
    (ADMIN_DATABASE_URL), exactly as migrations do.
    """
    recovery_manager = DatabaseSessionManager(recovery_url)
    try:
        # One replica: every `running` trace is a dead process's. Several: only turns whose
        # owner stopped heartbeating, so a joining replica leaves peers' turns be.
        await recover_interrupted_turns(
            recovery_manager,
            stale_after_seconds=LEASE_TTL_SECONDS if cluster_bus_enabled() else None,
        )
    finally:
        await recovery_manager.close()
