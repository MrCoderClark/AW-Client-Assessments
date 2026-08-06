"""Walk the audit_events hash chain and report the first bad row (if any).

Run:  uv run --env-file .env python scripts/verify_audit_chain.py [--limit N]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auth.audit import verify_chain
from auth.engine import dispose, transaction


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    async with transaction() as conn:
        r = await verify_chain(conn, limit=args.limit)

    if r["ok"]:
        print(f"[ok] chain verified — {r['checked']} row(s)")
    else:
        print(f"[FAIL] chain broken at row {r['first_bad']}")
        print(f"       reason: {r['reason']}")
        sys.exit(1)
    await dispose()


if __name__ == "__main__":
    asyncio.run(main())
