"""Sanity check: imports resolve, engine connects, permissions map is sane.

Run:  uv run --env-file .env python scripts/auth_smoke.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# ponytail: scripts/ isn't on sys.path when run directly; add repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from auth.context import ANONYMOUS, AuthContext
from auth.engine import dispose, engine, transaction
from auth.permissions import ROLE_PERMISSIONS, resolve_for_role
from auth.random import new_id, numeric_code, opaque_token
from auth.services import AuthService, PasswordService, SessionService, TokenService
from auth.settings import load


async def main() -> None:
    s = load()
    print(f"[ok] settings loaded — db url ends in .../{s.database_url.rsplit('/', 1)[-1]}")

    # Engine connect + expected tables present
    async with transaction() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' ORDER BY tablename"
            )
        )
        tables = [r[0] for r in rows]
        expected = {"users", "sessions", "audit_events", "rate_limit_buckets"}
        missing = expected - set(tables)
        assert not missing, f"missing tables: {missing}"
        print(f"[ok] engine connected — {len(tables)} tables visible")

    # Permissions map
    for role, perms in ROLE_PERMISSIONS.items():
        assert perms == resolve_for_role(role), role
    assert not resolve_for_role("bogus")
    assert "pdf:read" in ROLE_PERMISSIONS["viewer"]
    assert "user:delete" in ROLE_PERMISSIONS["admin"]
    assert "user:delete" not in ROLE_PERMISSIONS["operator"]
    print(f"[ok] permissions map — {sum(len(p) for p in ROLE_PERMISSIONS.values())} grants across 3 roles")

    # Context
    assert ANONYMOUS.actor_kind == "anonymous"
    assert not ANONYMOUS.is_authenticated
    ctx = AuthContext(actor_kind="user", user_id="u1", role="admin",
                      permissions=ROLE_PERMISSIONS["admin"], request_id="r1")
    assert ctx.is_authenticated
    assert "audit:read" in ctx.permissions
    print("[ok] AuthContext")

    # Random
    assert new_id() != new_id()
    assert len(opaque_token()) >= 40
    code = numeric_code(6)
    assert len(code) == 6 and code.isdigit()
    print("[ok] random primitives")

    # Services instantiate (stubs; methods raise NotImplementedError)
    pw = PasswordService(s)
    tk = TokenService(s)
    ss = SessionService(s)
    AuthService(s, passwords=pw, tokens=tk, sessions=ss)
    print("[ok] services instantiate")

    await dispose()
    print("[done]")


if __name__ == "__main__":
    asyncio.run(main())
