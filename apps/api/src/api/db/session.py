"""Database session management with PostgreSQL Row-Level Security (RLS) enforcement."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .alembic_config import upgrade_to_head

logger = logging.getLogger(__name__)

# The tenant RLS context for the work currently in flight.
#
# Migration 002 FORCEs row-level security on ten tables (sessions, mcp_servers,
# knowledge_*, custom_skills, agent_personas, subagent_configs, *_settings,
# user_credentials) with a policy keyed on `app.current_org_id`. A session that never
# sets that setting sees *nothing* and cannot write, so the tenant context is
# established once per request and applied by every session the request opens —
# relying on ~19 individual call sites to remember it is exactly how a silent
# "empty list in production" bug is born.
current_tenant_org_id: ContextVar[str | None] = ContextVar("current_tenant_org_id", default=None)


class DatabaseSessionManager:
    """Manages the lifecycle of async database engines, schema provisioning, and RLS policies."""

    def __init__(self, db_url: str | None = None) -> None:
        self.db_url = db_url
        self.engine: AsyncEngine | None = None
        self.session_factory: async_sessionmaker[AsyncSession] | None = None

        if self.db_url:
            self.init_engine(self.db_url)

    def init_engine(self, db_url: str) -> None:
        """Initialize SQLAlchemy async engine and sessionmaker."""
        self.db_url = db_url
        self.engine = create_async_engine(
            db_url,
            echo=False,
            future=True,
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    async def create_tables(self) -> None:
        """Bring the schema to the Alembic head (development and tests only).

        Alembic is the single source of truth for tables *and* row-level-security policies;
        this just runs the same migrations the container entrypoint and the Helm init
        container run, so a test database and a production database cannot drift apart.
        """
        if self.db_url is None or self.engine is None:
            raise RuntimeError("Database engine not initialized")
        await asyncio.to_thread(upgrade_to_head, self.db_url)

    async def close(self) -> None:
        """Dispose of the database engine and connection pool."""
        if self.engine:
            await self.engine.dispose()
            self.engine = None
            self.session_factory = None

    @asynccontextmanager
    async def session(self, tenant_org_id: str | None = None) -> AsyncIterator[AsyncSession]:
        """Provide a scoped database session with the tenant RLS context set.

        Falls back to the ambient request context (``current_tenant_org_id``) when no
        explicit tenant is given, so every session opened while handling a tenant
        request is correctly scoped — including the call sites that never passed an org.
        """
        if self.session_factory is None:
            raise RuntimeError("Database session factory is not initialized")
        effective_org_id = tenant_org_id or current_tenant_org_id.get()
        async with self.session_factory() as session:
            try:
                if effective_org_id:
                    await set_tenant_context(session, effective_org_id)
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise


async def set_tenant_context(session: AsyncSession, org_id: str) -> None:
    """Set the PostgreSQL transaction-local tenant context to enforce Row-Level Security."""
    bind = session.bind
    dialect_name = bind.dialect.name if bind else ""
    if dialect_name == "postgresql":
        # PostgreSQL does not allow parameters in 'SET LOCAL'.
        # 'SELECT set_config(setting_name, new_value, is_local)' supports parameterized bindings
        # and is_local=true binds strictly to the current transaction.
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)"),
            {"org_id": org_id},
        )
    session.info["tenant_org_id"] = org_id


# Global instance default for application runtime
db_manager = DatabaseSessionManager()


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an async database session."""
    if db_manager.session_factory is None:
        raise RuntimeError("Global DatabaseSessionManager is not configured with a db_url")
    async with db_manager.session() as session:
        yield session
