"""Runtime app settings — a thin typed accessor over the app_settings table.

Settings an admin flips from the UI (unlike env vars, which need a restart).
Values are JSONB; we read/write them as JSON text so the raw-SQL path is
deterministic regardless of the driver's JSONB decoding.

Keys + their meaning live in DEFAULTS; anything absent from the table falls
back to its default, so a missing row never breaks a read.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# key -> default value. Also the allow-list: PATCH rejects unknown keys.
DEFAULTS: dict[str, Any] = {
    # When False, Microsoft SSO sign-in is blocked; only local accounts log in.
    "sso_login_enabled": True,
    # When True, only emails in the sso_allowlist table may sign in via SSO.
    "sso_allowlist_enabled": False,
    # When True, every user must enrol app 2FA (TOTP) before completing login.
    "mfa_required": False,
}


async def get_all(conn: AsyncConnection) -> dict[str, Any]:
    """All settings, with defaults filled in for any missing row."""
    out = dict(DEFAULTS)
    rows = await conn.execute(text("SELECT key, value::text AS v FROM app_settings"))
    for r in rows:
        if r.key in DEFAULTS:
            try:
                out[r.key] = json.loads(r.v)
            except (ValueError, TypeError):
                pass  # keep the default on a malformed row
    return out


async def get_bool(conn: AsyncConnection, key: str, default: bool | None = None) -> bool:
    """One boolean setting. `default` overrides DEFAULTS when given."""
    r = await conn.execute(
        text("SELECT value::text AS v FROM app_settings WHERE key = :k"), {"k": key}
    )
    row = r.first()
    if row is None:
        return bool(DEFAULTS.get(key, False) if default is None else default)
    try:
        return bool(json.loads(row.v))
    except (ValueError, TypeError):
        return bool(DEFAULTS.get(key, False) if default is None else default)


async def set_many(
    conn: AsyncConnection, updates: dict[str, Any], *, actor_id: str | None
) -> dict[str, Any]:
    """Upsert the given keys. Unknown keys raise KeyError. Returns get_all()."""
    for k in updates:
        if k not in DEFAULTS:
            raise KeyError(k)
    for k, v in updates.items():
        await conn.execute(
            text("""
                INSERT INTO app_settings (key, value, updated_by, updated_at)
                VALUES (:k, CAST(:v AS jsonb), :a, now())
                ON CONFLICT (key)
                DO UPDATE SET value = CAST(:v AS jsonb), updated_by = :a, updated_at = now()
            """),
            {"k": k, "v": json.dumps(v), "a": actor_id},
        )
    return await get_all(conn)


# ---------- SSO allowlist -------------------------------------------

async def sso_allowlist_page(
    conn: AsyncConnection, *, q: str | None = None, limit: int = 50, offset: int = 0,
) -> dict[str, Any]:
    """One page of the allowlist, newest-added first, optionally filtered by a
    case-insensitive substring of the email. Returns {emails, total}."""
    limit = max(1, min(limit, 200))
    where, params = "", {"lim": limit, "off": max(0, offset)}
    if q and q.strip():
        where = "WHERE email_normalized LIKE :q"
        params["q"] = f"%{q.strip().lower()}%"
    total = (await conn.execute(
        text(f"SELECT count(*) AS n FROM sso_allowlist {where}"), params)).first().n
    rows = await conn.execute(
        text(f"""SELECT email, note, added_at FROM sso_allowlist {where}
                 ORDER BY added_at DESC, email LIMIT :lim OFFSET :off"""),
        params,
    )
    return {
        "emails": [{"email": r.email, "note": r.note,
                    "added_at": r.added_at.isoformat() if r.added_at else None} for r in rows],
        "total": int(total),
    }


async def sso_allowlist_add_many(
    conn: AsyncConnection, emails: list[str], note: str | None, actor_id: str | None,
) -> dict[str, Any]:
    """Bulk-add emails (paste/upload). Returns counts + the invalid ones.
    Existing entries are left untouched (counted as skipped)."""
    added, skipped, invalid, seen = 0, 0, [], set()
    for raw in emails:
        e = (raw or "").strip().strip(",;")
        if not e:
            continue
        norm = e.lower()
        if "@" not in norm or "." not in norm.split("@")[-1] or len(e) > 254:
            invalid.append(e)
            continue
        if norm in seen:
            continue
        seen.add(norm)
        r = await conn.execute(
            text("""
                INSERT INTO sso_allowlist (email_normalized, email, note, added_by, added_at)
                VALUES (:n, :e, :note, :a, now())
                ON CONFLICT (email_normalized) DO NOTHING
                RETURNING email_normalized
            """),
            {"n": norm, "e": e, "note": (note or None), "a": actor_id},
        )
        if r.first() is not None:
            added += 1
        else:
            skipped += 1
    return {"added": added, "skipped": skipped, "invalid": invalid}


async def sso_allowlist_remove(conn: AsyncConnection, email: str) -> None:
    await conn.execute(
        text("DELETE FROM sso_allowlist WHERE email_normalized = :n"),
        {"n": email.strip().lower()},
    )


async def is_sso_allowed(conn: AsyncConnection, email: str) -> bool:
    """True if the allowlist is disabled, or the email is on it. Enforced in the
    SSO callback for every sign-in (new or existing)."""
    if not await get_bool(conn, "sso_allowlist_enabled"):
        return True
    r = await conn.execute(
        text("SELECT 1 FROM sso_allowlist WHERE email_normalized = :n"),
        {"n": (email or "").strip().lower()},
    )
    return r.first() is not None
