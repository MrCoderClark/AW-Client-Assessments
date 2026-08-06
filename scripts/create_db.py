"""Idempotent: create the `clientfiles_v2` database if it doesn't exist.

Run:  uv run --env-file .env scripts/create_db.py

Reads `DATABASE_ADMIN_URL` (points at the maintenance `postgres` DB) and
extracts the target DB name from `DATABASE_URL`.
"""
import asyncio
import os
import re
import sys
from urllib.parse import urlparse

import asyncpg


def target_db_name() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL not set")
    # Strip the SQLAlchemy +driver so urlparse gets a clean scheme.
    plain = re.sub(r"^postgresql\+[a-z]+", "postgresql", url)
    return urlparse(plain).path.lstrip("/")


async def main() -> None:
    admin = os.environ.get("DATABASE_ADMIN_URL")
    if not admin:
        sys.exit("DATABASE_ADMIN_URL not set")
    name = target_db_name()
    if not name:
        sys.exit("could not parse DB name from DATABASE_URL")
    conn = await asyncpg.connect(admin)
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", name
        )
        if exists:
            print(f"[ok] database {name!r} already exists")
            return
        # ponytail: CREATE DATABASE can't run inside a transaction; asyncpg
        # runs statements outside a tx by default, which is what we want here.
        await conn.execute(f'CREATE DATABASE "{name}"')
        print(f"[new] created database {name!r}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
