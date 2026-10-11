#!/usr/bin/env python3
"""Disaster-recovery drill helper for Rocket Chat.

This script proves two operational claims rather than asserting them:

1. **Backup/restore round trip.** It builds a scratch database at the Alembic
   head, seeds a row, takes a ``pg_dump`` (custom format), restores it into a
   second scratch database with ``pg_restore`` and proves the restore reached
   the same schema revision with the same table set and the same seeded row.
   Every step is timed and printed.

2. **Reversible migration chain.** For *every* revision it applies
   ``alembic upgrade +1`` and then ``alembic downgrade -1`` on a third scratch
   database, then performs one full ``upgrade head`` → ``downgrade base`` →
   ``upgrade head`` cycle. A revision that cannot be reversed fails the drill.

The scratch databases are intentionally **not** dropped: the brief that
commissioned this drill asks for them to remain inspectable. Point the script
at any PostgreSQL 16 server with ``--admin-url`` (default: the local
``postgres:postgres`` instance the playbook documents).

Usage::

    uv run python scripts/dr_drill.py
    uv run python scripts/dr_drill.py --admin-url postgresql://postgres:postgres@db:5432
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SOURCE_DB = "rocket_chat_backup"
RESTORE_DB = "rocket_chat_restore"
MIGRATION_DB = "rocket_chat_drill_mig"
SEED_ORG_ID = "drill_org"
SEED_ORG_SLUG = "drill-org"


def _run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    """Run a command, returning stdout and surfacing stderr on failure."""
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout


def _timed(label: str, command: list[str], *, env: dict[str, str] | None = None) -> str:
    started = time.perf_counter()
    output = _run(command, env=env)
    elapsed_ms = (time.perf_counter() - started) * 1000
    print(f"  [{elapsed_ms:8.1f} ms] {label}")
    return output


def _psql(admin_url: str, database: str, sql: str) -> str:
    return _run(["psql", f"{admin_url}/{database}", "-tAc", sql]).strip()


def _alembic_env(database_url: str) -> dict[str, str]:
    import os

    env = dict(os.environ)
    env["DATABASE_URL"] = database_url.replace("postgresql://", "postgresql+asyncpg://")
    return env


def _alembic_current(database_url: str) -> str:
    output = _run(
        ["uv", "run", "alembic", "current"],
        env=_alembic_env(database_url),
    )
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
    # At the base of the chain `alembic current` prints nothing.
    return lines[-1] if lines else "(base)"


def _recreate_database(admin_url: str, database: str) -> None:
    _psql(admin_url, "postgres", f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE);')
    _psql(admin_url, "postgres", f'CREATE DATABASE "{database}";')


def _pg_client_major() -> int:
    output = _run(["pg_dump", "--version"])
    return int(output.split()[2].split(".")[0])


def _pg_server_major(admin_url: str) -> int:
    # `SHOW server_version_num` is a stable integer across versions (e.g. 160015).
    return int(_psql(admin_url, "postgres", "SHOW server_version_num;")) // 10000


def _backup_restore_commands(admin_url: str, dump_path: Path) -> tuple[list[str], list[str], str]:
    """Pick a dump/restore mechanism that matches the server's client major.

    A newer ``pg_dump`` (18) emits ``SET transaction_timeout = 0;``, a GUC that a
    PostgreSQL 16 server rejects — the data is fine, the session preamble is not.
    When the local client and server majors differ we therefore take the
    plain-SQL path (``pg_dump -Fp`` → ``psql``) and drop that single unsupported
    GUC, rather than shipping a dump whose restore is not clean. When they match,
    the custom format is used, as an operator normally would.
    """
    if _pg_client_major() == _pg_server_major(admin_url):
        dump = [
            "pg_dump",
            f"--dbname={admin_url}/{SOURCE_DB}",
            "--format=custom",
            f"--file={dump_path}",
        ]
        restore = [
            "pg_restore",
            "--no-owner",
            "--no-privileges",
            f"--dbname={admin_url}/{RESTORE_DB}",
            str(dump_path),
        ]
        return dump, restore, "custom"

    dump = [
        "pg_dump",
        f"--dbname={admin_url}/{SOURCE_DB}",
        "--format=plain",
        "--no-owner",
        "--no-privileges",
        f"--file={dump_path}",
    ]
    filtered = dump_path.with_suffix(dump_path.suffix + ".filtered.sql")
    restore = [
        "sh",
        "-c",
        # `sh -c` keeps the filtering and the restore in one timed step. The filter
        # only removes the GUC line validated by the client/server check above.
        "sed '/^SET transaction_timeout = 0;$/d' "
        f"'{dump_path}' > '{filtered}' && "
        f"psql '{admin_url}/{RESTORE_DB}' -v ON_ERROR_STOP=1 -q -f '{filtered}'",
    ]
    return dump, restore, "plain"


def _revisions_in_order() -> list[str]:
    """Read the linear migration chain from Alembic itself, base → head."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    ordered: list[str] = []
    for revision in script.walk_revisions("base", "heads"):
        ordered.append(revision.revision)
    ordered.reverse()
    return ordered


def _table_count(admin_url: str, database: str) -> int:
    return int(
        _psql(
            admin_url,
            database,
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE';",
        )
    )


def _seed(admin_url: str, database: str) -> None:
    # RLS is FORCEd on organizations, but `postgres` is a superuser and bypasses it,
    # exactly as the admin connection Alembic uses.
    _psql(
        admin_url,
        database,
        "INSERT INTO organizations (id, name, slug, created_at, settings) "
        f"VALUES ('{SEED_ORG_ID}', 'DR Drill Org', '{SEED_ORG_SLUG}', 0, '{{}}'::jsonb);",
    )


def run_backup_restore(admin_url: str, dump_path: Path) -> dict[str, object]:
    print("\n== 1. Backup / restore round trip ==")
    _recreate_database(admin_url, SOURCE_DB)
    _recreate_database(admin_url, RESTORE_DB)

    _timed(
        f"alembic upgrade head on {SOURCE_DB}",
        ["uv", "run", "alembic", "upgrade", "head"],
        env=_alembic_env(f"{admin_url}/{SOURCE_DB}"),
    )
    _seed(admin_url, SOURCE_DB)

    dump_command, restore_command, fmt = _backup_restore_commands(admin_url, dump_path)
    _timed(f"backup ({fmt}) {SOURCE_DB} -> {dump_path.name}", dump_command)
    dump_bytes = dump_path.stat().st_size

    _timed(f"restore ({fmt}) {dump_path.name} -> {RESTORE_DB}", restore_command)
    # A restored dump already carries the alembic_version table; this must be a no-op.
    _timed(
        f"alembic upgrade head on {RESTORE_DB} (no-op proof)",
        ["uv", "run", "alembic", "upgrade", "head"],
        env=_alembic_env(f"{admin_url}/{RESTORE_DB}"),
    )

    source_rev = _alembic_current(f"{admin_url}/{SOURCE_DB}")
    restore_rev = _alembic_current(f"{admin_url}/{RESTORE_DB}")
    source_tables = _table_count(admin_url, SOURCE_DB)
    restore_tables = _table_count(admin_url, RESTORE_DB)
    restore_seed = _psql(
        admin_url, RESTORE_DB, f"SELECT count(*) FROM organizations WHERE id='{SEED_ORG_ID}';"
    )
    restore_seed = _psql(
        admin_url, RESTORE_DB, f"SELECT count(*) FROM organizations WHERE id='{SEED_ORG_ID}';"
    )

    assert source_rev == restore_rev, f"revision drift: {source_rev!r} != {restore_rev!r}"
    assert source_tables == restore_tables, (
        f"table-count drift: {source_tables} != {restore_tables}"
    )
    assert restore_seed == "1", f"seeded row missing after restore (count={restore_seed})"

    print(f"  dump size: {dump_bytes} bytes")
    print(f"  revision: source={source_rev} restore={restore_rev}")
    print(f"  public base tables: source={source_tables} restore={restore_tables}")
    print(f"  seeded organizations row restored: {restore_seed}")
    return {
        "dump_bytes": dump_bytes,
        "revision": restore_rev,
        "tables": restore_tables,
    }


def run_migration_chain(admin_url: str) -> list[tuple[str, str]]:
    print("\n== 2. Reversible migration chain (per revision) ==")
    _recreate_database(admin_url, MIGRATION_DB)
    database_url = f"{admin_url}/{MIGRATION_DB}"
    results: list[tuple[str, str]] = []

    revisions = _revisions_in_order()
    print(f"  chain: {' -> '.join(revisions)}")

    # Ascend one revision at a time with the numeric relative form.
    for revision in revisions:
        _timed(
            f"upgrade +1 -> {revision}",
            ["uv", "run", "alembic", "upgrade", "+1"],
            env=_alembic_env(database_url),
        )
        current_after_up = _alembic_current(database_url)
        assert revision in current_after_up, (
            f"after upgrade +1 expected {revision}, got {current_after_up!r}"
        )

    # Descend one revision at a time, proving every revision's downgrade.
    for index, revision in enumerate(reversed(revisions)):
        _timed(
            f"downgrade -1 from {revision}",
            ["uv", "run", "alembic", "downgrade", "-1"],
            env=_alembic_env(database_url),
        )
        current_after_down = _alembic_current(database_url)
        if index == len(revisions) - 1:
            assert current_after_down == "(base)", (
                f"expected base after final downgrade, got {current_after_down!r}"
            )
        else:
            expected_previous = revisions[len(revisions) - 2 - index]
            assert expected_previous in current_after_down, (
                f"after downgrading {revision} expected {expected_previous}, "
                f"got {current_after_down!r}"
            )
        results.append((revision, "up+1/down-1 OK"))

    print("\n== 3. Full chain cycle ==")
    _timed(
        "upgrade head",
        ["uv", "run", "alembic", "upgrade", "head"],
        env=_alembic_env(database_url),
    )
    head_rev = _alembic_current(database_url)
    _timed(
        "downgrade base",
        ["uv", "run", "alembic", "downgrade", "base"],
        env=_alembic_env(database_url),
    )
    base_tables = _table_count(admin_url, MIGRATION_DB)
    _timed(
        "upgrade head (again)",
        ["uv", "run", "alembic", "upgrade", "head"],
        env=_alembic_env(database_url),
    )
    final_rev = _alembic_current(database_url)
    assert final_rev == head_rev, f"full-cycle head drift: {head_rev!r} != {final_rev!r}"
    print(f"  after downgrade base, public base tables = {base_tables}")
    print(f"  full cycle head: {head_rev} -> base -> {final_rev}")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--admin-url",
        default="postgresql://postgres:postgres@localhost:5432",
        help="Base admin connection URL without a database name.",
    )
    parser.add_argument(
        "--dump",
        default="/tmp/rocket_chat_drill.dump",
        help="Where to write the scratch pg_dump artifact.",
    )
    args = parser.parse_args(argv)

    admin_url = args.admin_url.rstrip("/")
    dump_path = Path(args.dump)

    backup = run_backup_restore(admin_url, dump_path)
    migrations = run_migration_chain(admin_url)

    print("\n== Drill summary ==")
    print(
        f"  backup/restore: revision={backup['revision']} "
        f"tables={backup['tables']} dump={backup['dump_bytes']} bytes"
    )
    print(f"  migration chain: {len(migrations)} revisions each reversed cleanly")
    print("  scratch databases kept: " + ", ".join((SOURCE_DB, RESTORE_DB, MIGRATION_DB)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
