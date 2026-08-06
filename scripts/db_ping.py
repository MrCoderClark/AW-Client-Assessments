"""Quick smoke test: connect to clientfiles_v2 and list tables + enums.

Run:  uv run --env-file .env python scripts/db_ping.py
"""
import asyncio
import os
import re
import sys

import asyncpg


async def main() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL not set")
    # asyncpg wants the plain scheme, no +driver suffix.
    plain = re.sub(r"^postgresql\+[a-z]+", "postgresql", url)
    conn = await asyncpg.connect(plain)
    try:
        version = await conn.fetchval("SELECT version()")
        db = await conn.fetchval("SELECT current_database()")
        tables = await conn.fetch(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' ORDER BY tablename"
        )
        enums = await conn.fetch(
            "SELECT typname FROM pg_type "
            "WHERE typtype = 'e' AND typnamespace = 'public'::regnamespace "
            "ORDER BY typname"
        )
        print(f"[ok] connected to {db!r}")
        print(f"     {version.split(',')[0]}")
        print(f"     {len(tables)} tables:")
        for t in tables:
            print(f"       - {t['tablename']}")
        print(f"     {len(enums)} enums:")
        for e in enums:
            print(f"       - {e['typname']}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
