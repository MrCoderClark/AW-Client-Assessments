"""Location scoping — which offices' rows a caller may see.

Location is orthogonal to role: role gates *actions*, location gates *data*. A
caller with `location:all` (admin / HQ) sees every office; everyone else is limited
to the offices in their `user_locations`. A user with no office assigned sees
nothing (fail-closed). See docs/MULTI_LOCATION.md.
"""
from __future__ import annotations

from auth.context import AuthContext


def allowed_location_ids(ctx: AuthContext, conn) -> list[int] | None:
    """Office ids the caller may see.

    - `None`  → every office (caller holds `location:all`).
    - `[...]` → exactly these offices.
    - `[]`    → no office assigned → sees nothing (fail-closed).
    """
    if "location:all" in ctx.permissions:
        return None
    if not ctx.user_id:
        return []
    rows = conn.execute(
        "SELECT location_id FROM user_locations WHERE user_id = %s", (ctx.user_id,)
    ).fetchall()
    return [r["location_id"] for r in rows]


def location_ok(ctx: AuthContext, conn, location_id: int | None) -> bool:
    """Whether the caller may access a single row in this office."""
    allowed = allowed_location_ids(ctx, conn)
    return allowed is None or location_id in allowed


def filter_ids_in_scope(ctx: AuthContext, conn, ids: list[int]) -> list[int]:
    """Keep only the pdf ids whose office the caller may act on (order preserved).

    Silently drops out-of-office ids so a bulk write can't touch another office's
    files by id. `location:all` keeps everything.
    """
    if not ids:
        return []
    allowed = allowed_location_ids(ctx, conn)
    if allowed is None:
        return list(ids)
    if not allowed:
        return []
    idph = ",".join(["%s"] * len(ids))
    locph = ",".join(["%s"] * len(allowed))
    ok = {r["id"] for r in conn.execute(
        f"SELECT id FROM pdfs WHERE id IN ({idph}) AND location_id IN ({locph})",
        (*ids, *allowed),
    ).fetchall()}
    return [i for i in ids if i in ok]


def scope_sql(ids: list[int] | None, col: str = "location_id") -> tuple[str, list]:
    """A (clause, params) pair to AND into a query.

    `None` → "TRUE" (no restriction); `[]` → "FALSE" (deny all).
    """
    if ids is None:
        return "TRUE", []
    if not ids:
        return "FALSE", []
    placeholders = ",".join(["%s"] * len(ids))
    return f"{col} IN ({placeholders})", list(ids)
