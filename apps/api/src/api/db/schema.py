"""Schema version checks: Alembic is the only thing that changes the schema.

The API connects as the row-level-security application role, which must not own or alter
tables. Migrations run once per release (Helm hook, entrypoint) as the schema owner; at startup
the API only *verifies* it is talking to a database at the revision it was built for.
"""

from __future__ import annotations

import logging

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from api.db.alembic_config import find_alembic_config
from api.db.session import DatabaseSessionManager

logger = logging.getLogger(__name__)


class SchemaNotMigratedError(RuntimeError):
    """The database is not at the Alembic head this build expects."""


def head_revision() -> str | None:
    """The newest revision in this build's migration scripts, if they are shipped with it."""
    ini = find_alembic_config()
    if ini is None:
        return None
    config = Config(str(ini))
    config.set_main_option("script_location", str(ini.parent / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


async def current_revision(manager: DatabaseSessionManager) -> str | None:
    """The revision recorded in the database, or ``None`` when it was never migrated."""
    async with manager.session() as session:
        exists = await session.scalar(text("SELECT to_regclass('public.alembic_version')"))
        if exists is None:
            return None
        version = await session.scalar(text("SELECT version_num FROM alembic_version"))
        return str(version) if version is not None else None


async def assert_schema_current(manager: DatabaseSessionManager) -> None:
    """Raise ``SchemaNotMigratedError`` unless the database is at this build's head."""
    expected = head_revision()
    if expected is None:
        logger.warning("Migration scripts are not shipped with this build; schema not verified.")
        return
    actual = await current_revision(manager)
    if actual != expected:
        raise SchemaNotMigratedError(
            f"Database schema is at revision {actual or 'none'} but this build needs {expected}. "
            "Run `alembic upgrade head` as the schema owner (the Helm chart does this in a "
            "pre-upgrade Job; the container entrypoint does it when ADMIN_DATABASE_URL is set)."
        )
