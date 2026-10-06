"""Database session management with PostgreSQL Row-Level Security (RLS) enforcement."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .models import Base

logger = logging.getLogger(__name__)


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
        """Create all tables in the database if they do not exist."""
        if self.engine is None:
            raise RuntimeError("Database engine not initialized")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await self.apply_rls_policies(conn)

    async def apply_rls_policies(self, conn: AsyncConnection) -> None:
        """Configure Row-Level Security on tenant-isolated tables in PostgreSQL."""
        if conn.dialect.name != "postgresql":
            logger.debug(
                "Skipping PostgreSQL RLS setup for non-Postgres dialect (%s)", conn.dialect.name
            )
            return

        rls_statements = [
            # Enable RLS on sessions
            "ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;",
            "ALTER TABLE sessions FORCE ROW LEVEL SECURITY;",
            "DROP POLICY IF EXISTS tenant_isolation_sessions ON sessions;",
            """
            CREATE POLICY tenant_isolation_sessions ON sessions
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """,
            # Enable RLS on byok_credentials
            "ALTER TABLE byok_credentials ENABLE ROW LEVEL SECURITY;",
            "ALTER TABLE byok_credentials FORCE ROW LEVEL SECURITY;",
            "DROP POLICY IF EXISTS tenant_isolation_byok ON byok_credentials;",
            """
            CREATE POLICY tenant_isolation_byok ON byok_credentials
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """,
            # Enable RLS on org_settings
            "ALTER TABLE org_settings ENABLE ROW LEVEL SECURITY;",
            "ALTER TABLE org_settings FORCE ROW LEVEL SECURITY;",
            "DROP POLICY IF EXISTS tenant_isolation_org_settings ON org_settings;",
            """
            CREATE POLICY tenant_isolation_org_settings ON org_settings
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """,
            # Enable RLS on user_settings
            "ALTER TABLE user_settings ENABLE ROW LEVEL SECURITY;",
            "ALTER TABLE user_settings FORCE ROW LEVEL SECURITY;",
            "DROP POLICY IF EXISTS tenant_isolation_user_settings ON user_settings;",
            """
            CREATE POLICY tenant_isolation_user_settings ON user_settings
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """,
            # Enable RLS on user_credentials
            "ALTER TABLE user_credentials ENABLE ROW LEVEL SECURITY;",
            "ALTER TABLE user_credentials FORCE ROW LEVEL SECURITY;",
            "DROP POLICY IF EXISTS tenant_isolation_user_credentials ON user_credentials;",
            """
            CREATE POLICY tenant_isolation_user_credentials ON user_credentials
                FOR ALL
                USING (org_id = current_setting('app.current_org_id', true));
            """,
        ]
        for stmt in rls_statements:
            try:
                await conn.execute(text(stmt))
            except Exception as err:
                logger.warning("Failed executing RLS statement: %s; error: %s", stmt.strip(), err)

    async def close(self) -> None:
        """Dispose of the database engine and connection pool."""
        if self.engine:
            await self.engine.dispose()
            self.engine = None
            self.session_factory = None

    @asynccontextmanager
    async def session(self, tenant_org_id: str | None = None) -> AsyncIterator[AsyncSession]:
        """Provide a scoped database session with tenant RLS context set."""
        if self.session_factory is None:
            raise RuntimeError("Database session factory is not initialized")
        async with self.session_factory() as session:
            try:
                if tenant_org_id:
                    await set_tenant_context(session, tenant_org_id)
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
