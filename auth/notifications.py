"""In-app notifications + realtime SSE bus.

Design
------
- Fanout on emit: `emit_for_category()` looks up all currently-active users
  whose role receives that category and inserts one `notifications` row per
  recipient in a single INSERT ... SELECT. Also publishes the payload to any
  live SSE subscribers for those users.
- Categories → allowed roles:
    scan_commit    → admin, operator, viewer   (everyone sees ops events)
    security       → admin, operator            (SEC audit rows)
    user_lifecycle → admin, operator            (invite/suspend/reactivate)
    pc_health      → admin, operator            (PC unreachable / long silence)
- The SSE bus is an in-process pub/sub (dict of user_id → set[asyncio.Queue]).
  Single-process app, so no Redis, no NATS. Multi-worker: reconnect after a
  reload catches missed rows via the `since_id` param.

ponytail: one file, no extra deps. Row fanout is fine at ≤10 real users.
Upgrade path (a shared events table + per-user read cursor) noted inline.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .engine import transaction
from .random import new_id

# ---------- category → recipient roles -------------------------------

_ROLE_MATRIX: dict[str, tuple[str, ...]] = {
    "scan_commit":    ("admin", "operator"),
    "security":       ("admin", "operator"),
    "user_lifecycle": ("admin", "operator"),
    "pc_health":      ("admin", "operator"),
}
# Viewers never receive category broadcasts. They only see notifications
# targeted at their own user_id via `emit_for_user` (e.g. their password
# was force-reset by an admin).


# ---------- value objects --------------------------------------------

@dataclass(frozen=True)
class NotificationEvent:
    category: str
    kind: str
    title: str
    body: str = ""
    url: str | None = None
    severity: str = "INFO"       # INFO | WARN | SEC
    context: dict[str, Any] = field(default_factory=dict)


# ---------- in-process pub/sub for SSE -------------------------------

class _Bus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, user_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        async with self._lock:
            self._subs.setdefault(user_id, set()).add(q)
        return q

    async def unsubscribe(self, user_id: str, q: asyncio.Queue) -> None:
        async with self._lock:
            subs = self._subs.get(user_id)
            if not subs:
                return
            subs.discard(q)
            if not subs:
                self._subs.pop(user_id, None)

    async def publish(self, user_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._subs.get(user_id, ()))
        for q in targets:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # ponytail: drop rather than block; client reconciles on
                # next `since_id` reconnect.
                pass


_bus = _Bus()

# Reference to the primary asyncio loop (set at lifespan startup) so
# fire-and-forget calls from worker threads (scan/commit generators) can
# schedule SSE publishes without owning a loop themselves.
_main_loop: asyncio.AbstractEventLoop | None = None


def bus() -> _Bus:
    return _bus


def register_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Called once from FastAPI lifespan startup."""
    global _main_loop
    _main_loop = loop


# ---------- write path -----------------------------------------------

async def emit_for_category(event: NotificationEvent) -> int:
    """Insert one notifications row per active user whose role sees this
    category, then push to any live SSE subscribers. Runs in its own
    committed transaction — the caller's request tx may roll back on any
    error path and we still want notifications to persist.

    Returns the number of rows inserted (i.e. active recipients).
    """
    roles = _ROLE_MATRIX.get(event.category)
    if not roles:
        return 0

    ctx_json = json.dumps(event.context, default=str)
    async with transaction() as conn:
        r = await conn.execute(
            text("""
                INSERT INTO notifications
                    (id, user_id, category, kind, severity, title, body, url, context_json)
                SELECT
                    gen_random_uuid(), u.id,
                    :cat, :kind, :sev, :title, :body, :url, CAST(:ctx AS jsonb)
                FROM users u
                WHERE u.status = 'ACTIVE'
                  AND u.deleted_at IS NULL
                  AND u.role = ANY(CAST(:roles AS user_role[]))
                RETURNING id, user_id, created_at
            """),
            {
                "cat": event.category, "kind": event.kind, "sev": event.severity,
                "title": event.title, "body": event.body or None, "url": event.url,
                "ctx": ctx_json, "roles": list(roles),
            },
        )
        rows = r.all()

    # Publish to live streams (after commit — subscribers get real rows only).
    for row in rows:
        payload = {
            "id": str(row.id),
            "user_id": str(row.user_id),
            "category": event.category,
            "kind": event.kind,
            "severity": event.severity,
            "title": event.title,
            "body": event.body or "",
            "url": event.url,
            "context": event.context,
            "created_at": row.created_at.isoformat(),
            "read_at": None,
        }
        await _bus.publish(str(row.user_id), payload)
    return len(rows)


async def emit_for_user(user_id: str, event: NotificationEvent) -> str | None:
    """Insert a single notification for one user + publish. Returns the id."""
    ctx_json = json.dumps(event.context, default=str)
    nid = new_id()
    async with transaction() as conn:
        await conn.execute(
            text("""
                INSERT INTO notifications
                    (id, user_id, category, kind, severity, title, body, url, context_json)
                VALUES
                    (:id, :uid, :cat, :kind, :sev, :title, :body, :url, CAST(:ctx AS jsonb))
            """),
            {
                "id": nid, "uid": user_id, "cat": event.category, "kind": event.kind,
                "sev": event.severity, "title": event.title, "body": event.body or None,
                "url": event.url, "ctx": ctx_json,
            },
        )
    await _bus.publish(user_id, {
        "id": nid, "user_id": user_id, "category": event.category,
        "kind": event.kind, "severity": event.severity, "title": event.title,
        "body": event.body or "", "url": event.url, "context": event.context,
        "created_at": datetime.now(UTC).isoformat(), "read_at": None,
    })
    return nid


# ---------- read path (used by /api/v1/notifications) ---------------

async def list_for_user(
    conn: AsyncConnection, *, user_id: str, limit: int = 25, offset: int = 0,
    since_id: str | None = None,
    category: str | None = None, severity: str | None = None,
    unread_only: bool = False,
) -> tuple[list[dict], int, int]:
    """Recent notifications for one user, newest first.

    Returns (rows, total_matching, unread_count). `total_matching` reflects
    the WHERE filters; `unread_count` is always the user's total unread.
    `since_id` short-circuits pagination — SSE reconnect callers use it to
    catch up on rows newer than the last one they saw.
    """
    where = ["user_id = :uid"]
    params: dict = {"uid": user_id, "lim": limit, "off": offset}
    if since_id:
        where.append(
            "created_at > (SELECT created_at FROM notifications WHERE id = :sid)"
        )
        params["sid"] = since_id
    if category:
        where.append("category = :cat")
        params["cat"] = category
    if severity:
        where.append("severity = :sev")
        params["sev"] = severity
    if unread_only:
        where.append("read_at IS NULL")
    wh = " AND ".join(where)

    r = await conn.execute(
        text(f"""
            SELECT id, category, kind, severity, title, body, url,
                   context_json, read_at, created_at
            FROM notifications
            WHERE {wh}
            ORDER BY created_at DESC
            LIMIT :lim OFFSET :off
        """),
        params,
    )
    rows = r.all()

    total_r = await conn.execute(
        text(f"SELECT COUNT(*) FROM notifications WHERE {wh}"),
        {k: v for k, v in params.items() if k not in ("lim", "off")},
    )
    total = int(total_r.scalar_one() or 0)

    u = await conn.execute(
        text("SELECT COUNT(*) FROM notifications WHERE user_id = :uid AND read_at IS NULL"),
        {"uid": user_id},
    )
    unread = int(u.scalar_one() or 0)
    return [_row_to_dict(row) for row in rows], total, unread


async def mark_read(conn: AsyncConnection, *, user_id: str, notif_id: str) -> bool:
    r = await conn.execute(
        text("""
            UPDATE notifications SET read_at = now()
            WHERE id = :id AND user_id = :uid AND read_at IS NULL
        """),
        {"id": notif_id, "uid": user_id},
    )
    return (r.rowcount or 0) > 0


async def mark_all_read(conn: AsyncConnection, *, user_id: str) -> int:
    r = await conn.execute(
        text("""
            UPDATE notifications SET read_at = now()
            WHERE user_id = :uid AND read_at IS NULL
        """),
        {"uid": user_id},
    )
    return int(r.rowcount or 0)


def _row_to_dict(row) -> dict:
    return {
        "id": str(row.id),
        "category": row.category,
        "kind": row.kind,
        "severity": row.severity,
        "title": row.title,
        "body": row.body or "",
        "url": row.url,
        "context": row.context_json or {},
        "read_at": row.read_at.isoformat() if row.read_at else None,
        "created_at": row.created_at.isoformat(),
    }


# ---------- sync helper for non-async callers (scan/commit) ---------

def emit_sync(event: NotificationEvent) -> None:
    """Fire-and-forget wrapper for sync callers (scan.py / commit.py).

    - Inside an event loop: schedule the async fanout.
    - Outside any loop (worker thread): insert via sync psycopg to avoid
      cross-loop reuse of the async engine's pool, then hop back to the
      captured main loop to publish the SSE payload.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        loop.create_task(emit_for_category(event))
        return
    _emit_sync_psycopg(event)


def _emit_sync_psycopg(event: NotificationEvent) -> None:
    """Sync fanout: mirrors emit_for_category's INSERT...SELECT via psycopg,
    then hops the SSE publish to the captured main loop."""
    from db import connect as sync_connect  # local import — db is sync-only
    roles = _ROLE_MATRIX.get(event.category)
    if not roles:
        return
    ctx_json = json.dumps(event.context, default=str)
    role_array = "{" + ",".join(roles) + "}"
    conn = sync_connect()
    try:
        rows = conn.execute(
            """INSERT INTO notifications
                  (id, user_id, category, kind, severity, title, body, url, context_json)
                SELECT gen_random_uuid(), u.id, %s, %s, %s, %s, %s, %s, %s::jsonb
                FROM users u
                WHERE u.status = 'ACTIVE' AND u.deleted_at IS NULL
                  AND u.role = ANY(%s::user_role[])
                RETURNING id, user_id, created_at""",
            (event.category, event.kind, event.severity, event.title,
             event.body or None, event.url, ctx_json, role_array),
        ).fetchall()
        conn.commit()
    finally:
        conn.close()

    if _main_loop is None or _main_loop.is_closed() or not rows:
        return
    for row in rows:
        payload = {
            "id": str(row["id"]),
            "user_id": str(row["user_id"]),
            "category": event.category, "kind": event.kind, "severity": event.severity,
            "title": event.title, "body": event.body or "", "url": event.url,
            "context": event.context,
            "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
            "read_at": None,
        }
        try:
            asyncio.run_coroutine_threadsafe(
                _bus.publish(str(row["user_id"]), payload), _main_loop,
            )
        except RuntimeError:
            # Main loop shutting down — best-effort, drop.
            pass
