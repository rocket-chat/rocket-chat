"""The schema history is one line: two heads make `alembic upgrade head` refuse to run."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_migrations_have_a_single_head() -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))

    assert len(ScriptDirectory.from_config(config).get_heads()) == 1
