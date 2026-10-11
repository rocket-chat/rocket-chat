#!/usr/bin/env python3
"""Re-encrypt ``byok_credentials`` after an ``ENCRYPTION_MASTER_KEY`` rotation.

``CredentialCipher`` derives one AES-256-GCM key from the configured master key
string and has no key versioning: changing the environment variable makes every
previously stored ciphertext undecryptable. A rotation is therefore a
**re-encryption migration**, not a config change — this helper performs it:

    old master key ──decrypt──► plaintext ──encrypt──► new master key

It is a dry run by default (counts what it would change and never writes); pass
``--apply`` to persist. It runs on the admin connection (RLS is bypassed by the
superuser), never logs a secret, and is idempotent-ish: a row that fails to
decrypt with the old key is reported and left untouched rather than corrupted.

Usage::

    uv run python scripts/rotate_master_key.py \
        --database-url postgresql://postgres:postgres@localhost:5432/rocket_chat \
        --old-key "$OLD_ENCRYPTION_MASTER_KEY" \
        --new-key "$NEW_ENCRYPTION_MASTER_KEY" [--apply]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# `config_engine` imports the `specifications` contracts at the repository root, so both
# path roots must be importable when the script runs outside the workspace's editable venv.
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "packages", "config-engine", "src"))

from config_engine.crypto import CredentialCipher  # noqa: E402

SELECT_SQL = "SELECT id, encrypted_api_key FROM byok_credentials ORDER BY id;"
UPDATE_SQL = "UPDATE byok_credentials SET encrypted_api_key = $2 WHERE id = $1;"


async def rotate(database_url: str, old_key: str, new_key: str, apply: bool) -> int:
    import asyncpg

    old_cipher = CredentialCipher(master_key=old_key)
    new_cipher = CredentialCipher(master_key=new_key)

    connection = await asyncpg.connect(dsn=database_url)
    try:
        rows = await connection.fetch(SELECT_SQL)
        rotated = 0
        failures: list[str] = []
        for row in rows:
            try:
                plaintext = old_cipher.decrypt(row["encrypted_api_key"])
            except Exception:
                # Do not echo the ciphertext or the exception body: it may contain key
                # material on a partial-decrypt error.
                failures.append(row["id"])
                continue
            re_encrypted = new_cipher.encrypt(plaintext)
            if apply:
                await connection.execute(UPDATE_SQL, row["id"], re_encrypted)
            rotated += 1

        print(f"rows scanned: {len(rows)}")
        print(f"rows re-encrypted{'' if apply else ' (dry run, not written)'}: {rotated}")
        if failures:
            print(f"rows that did NOT decrypt with the old key: {len(failures)}")
            for row_id in failures:
                print(f"  - {row_id}")
        return 0 if not failures else 1
    finally:
        await connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.getenv("ADMIN_DATABASE_URL")
        or "postgresql://postgres:postgres@localhost:5432/rocket_chat",
    )
    parser.add_argument("--old-key", required=True, help="The master key currently in use.")
    parser.add_argument("--new-key", required=True, help="The master key to rotate to.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the re-encrypted values (default is a dry run).",
    )
    args = parser.parse_args(argv)
    return asyncio.run(rotate(args.database_url, args.old_key, args.new_key, args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
