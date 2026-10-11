"""Locating and driving the Alembic migrations from inside the application."""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config


def find_alembic_config() -> Path | None:
    """``alembic.ini`` from ``ALEMBIC_CONFIG``, the working directory or the source tree."""
    candidates = [os.getenv("ALEMBIC_CONFIG"), "alembic.ini"]
    candidates += [str(parent / "alembic.ini") for parent in Path(__file__).resolve().parents]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def upgrade_to_head(database_url: str) -> None:
    """Run ``alembic upgrade head`` against ``database_url`` (blocking; call from a thread)."""
    ini = find_alembic_config()
    if ini is None:
        raise RuntimeError("alembic.ini not found; cannot create the schema")
    config = Config(str(ini))
    config.set_main_option("script_location", str(ini.parent / "migrations"))
    config.attributes["configure_logger"] = False
    # migrations/env.py reads the target from the environment.
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
