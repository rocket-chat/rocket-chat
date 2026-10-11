"""Alembic async environment configuration for Rocket Chat database migrations."""

import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Ensure python path includes apps/api/src and packages
sys.path.insert(0, os.path.abspath("apps/api/src"))

from api.db.models import Base

config = context.config

# Embedded runs (tests, development) keep the host application's logging.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

default_db_url = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/rocket_chat",
)
config.set_main_option("sqlalchemy.url", default_db_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


# Arbitrary application-wide key; two replicas migrating at once queue on it instead of racing DDL.
MIGRATION_LOCK_KEY = 727_274_001


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    is_postgres = connection.dialect.name == "postgresql"
    if is_postgres:
        connection.execute(text(f"SELECT pg_advisory_lock({MIGRATION_LOCK_KEY})"))
        connection.commit()
    try:
        with context.begin_transaction():
            context.run_migrations()
    finally:
        if is_postgres:
            connection.execute(text(f"SELECT pg_advisory_unlock({MIGRATION_LOCK_KEY})"))
            connection.commit()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine and associate a connection with the context."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
