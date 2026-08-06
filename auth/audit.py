"""Audit logger — append hash-chained rows to audit_events.

Each row's `hash = SHA-256(prev_hash || canonical_json(row_sans_hashes))`.
Genesis prev_hash is 64 hex zeros. Writers serialize via a Postgres
advisory transaction lock so concurrent handlers can't fork the chain.

ponytail: writes are synchronous per request for M1 — Postgres inserts
under a session-scoped lock cost microseconds at LAN load, and skipping
the queue-and-flusher machinery keeps the M1 surface small. The design
doc's async writer stays available for later if p95 measurably slips.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .context import ANONYMOUS, AuthContext
from .random import new_id

GENESIS_HASH = "0" * 64
# Any integer stable across processes will do — hashtext('audit_events').
# Precomputed once so we don't pay for it every emit.
_ADVISORY_LOCK_KEY = 4_293_768_010  # int(hashlib.md5(b'audit_events').hexdigest()[:8], 16) mod 2**31


@dataclass(frozen=True)
class AuditEvent:
    action: str                                # 'AUTH_LOGIN_SUCCESS', ...
    outcome: str = "success"                    # success | failure | denied
    severity: str = "INFO"                      # INFO | WARN | SEC
    target_type: str | None = None
    target_id: str | None = None
    context: dict[str, Any] = field(default_factory=dict)


def _canonical_json(row: dict[str, Any]) -> str:
    """Deterministic JSON encoding used for the chain hash."""
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _compute_hash(prev_hash: str, row: dict[str, Any]) -> str:
    to_hash = f"{prev_hash}|{_canonical_json(row)}".encode("utf-8")
    return hashlib.sha256(to_hash).hexdigest()


class AuditLogger:
    """One instance per process. `emit()` opens its own committed transaction
    so audits survive even when the caller's request tx rolls back (which is
    exactly what happens on any failure path that raises).
    """

    async def emit(
        self,
        conn: AsyncConnection | None,
        ctx: AuthContext | None,
        event: AuditEvent,
        *,
        actor_ip: str | None = None,
        user_agent: str | None = None,
    ) -> str:
        """Append a row. Returns the new row id.

        Pass `conn=None` (the common case) to open a fresh committed tx —
        this way audit rows survive even when the caller's tx rolls back
        on a raised exception. Pass an existing connection ONLY when you
        need the audit + other writes to be atomic together (e.g. inside
        `_commit_reuse_security` which does multiple related INSERTs).

        Serializes with `pg_advisory_xact_lock` so the chain never forks
        when two requests write concurrently.
        """
        if conn is None:
            from .engine import transaction
            async with transaction() as own_conn:
                return await self._write(own_conn, ctx, event,
                                         actor_ip=actor_ip, user_agent=user_agent)
        return await self._write(conn, ctx, event, actor_ip=actor_ip, user_agent=user_agent)

    async def _write(
        self,
        conn: AsyncConnection,
        ctx: AuthContext | None,
        event: AuditEvent,
        *,
        actor_ip: str | None = None,
        user_agent: str | None = None,
    ) -> str:
        c = ctx or ANONYMOUS
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(:k)"),
            {"k": _ADVISORY_LOCK_KEY},
        )
        prev = await conn.execute(
            text("SELECT hash FROM audit_events ORDER BY at DESC, id DESC LIMIT 1")
        )
        prev_row = prev.first()
        prev_hash = prev_row.hash if prev_row is not None else GENESIS_HASH

        row_id = new_id()
        # The row we actually store — this same dict feeds the hash.
        row = {
            "id": row_id,
            "actor_id": c.user_id,
            "actor_type": c.actor_kind,
            "action": event.action,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "ip_address": actor_ip,
            "user_agent": user_agent,
            "request_id": c.request_id or "",
            "outcome": event.outcome,
            "severity": event.severity,
            "context_json": event.context or {},
        }
        chained = _compute_hash(prev_hash, row)

        await conn.execute(
            text("""
                INSERT INTO audit_events
                    (id, at, actor_id, actor_type, action, target_type, target_id,
                     ip_address, user_agent, request_id, outcome, severity,
                     context_json, prev_hash, hash)
                VALUES
                    (:id, now(), :actor_id, :actor_type, :action, :target_type, :target_id,
                     :ip, :ua, :req, :outcome, :severity,
                     CAST(:ctx AS jsonb), :prev, :hash)
            """),
            {
                "id": row_id,
                "actor_id": row["actor_id"],
                "actor_type": row["actor_type"],
                "action": row["action"],
                "target_type": row["target_type"],
                "target_id": row["target_id"],
                "ip": row["ip_address"],
                "ua": row["user_agent"],
                "req": row["request_id"],
                "outcome": row["outcome"],
                "severity": row["severity"],
                "ctx": _canonical_json(row["context_json"]),
                "prev": prev_hash,
                "hash": chained,
            },
        )
        return row_id


# Process-wide singleton — the logger has no per-call state.
_logger = AuditLogger()


def audit_logger() -> AuditLogger:
    return _logger


# ---------- sync writer for scan/commit/archive workers -------------
#
# The async AuditLogger above is the request-path writer. Background
# workers (scan.py, commit.py, the archive service) run outside any
# event loop and can't use it. `emit_sync` mirrors `_write` exactly —
# same hash-chain, same advisory-lock serialization — via psycopg.
#
# Runs in its own committed transaction so audit rows survive even
# when the worker's own DB work fails partway through.

def emit_sync(
    action: str,
    *,
    actor_id: str | None = None,
    actor_type: str = "system",
    target_type: str | None = None,
    target_id: str | None = None,
    outcome: str = "success",
    severity: str = "INFO",
    context: dict[str, Any] | None = None,
    request_id: str = "",
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Append one audit row from sync code. Returns the new row id.

    `actor_id` is the UUID string of the admin who triggered the bulk op;
    None with actor_type='system' for scheduler/repair runs.
    """
    from db import connect as sync_connect  # local — db is sync-only
    from .random import new_id

    ctx = context or {}
    row_id = new_id()
    # Same shape the async _write hashes over — keep in lockstep with it.
    row = {
        "id": row_id,
        "actor_id": actor_id,
        "actor_type": actor_type,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "ip_address": ip_address,
        "user_agent": user_agent,
        "request_id": request_id,
        "outcome": outcome,
        "severity": severity,
        "context_json": ctx,
    }

    conn = sync_connect()
    try:
        # Advisory xact lock + prev-hash read + insert all inside the
        # same tx so concurrent writers can't read the same prev_hash.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_ADVISORY_LOCK_KEY,))
        prev_row = conn.execute(
            "SELECT hash FROM audit_events ORDER BY at DESC, id DESC LIMIT 1"
        ).fetchone()
        prev_hash = prev_row["hash"] if prev_row is not None else GENESIS_HASH
        chained = _compute_hash(prev_hash, row)
        conn.execute(
            """
            INSERT INTO audit_events
                (id, at, actor_id, actor_type, action, target_type, target_id,
                 ip_address, user_agent, request_id, outcome, severity,
                 context_json, prev_hash, hash)
            VALUES
                (%s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                 %s::jsonb, %s, %s)
            """,
            (row_id, actor_id, actor_type, action, target_type, target_id,
             ip_address, user_agent, request_id, outcome, severity,
             _canonical_json(ctx), prev_hash, chained),
        )
        conn.commit()
    finally:
        conn.close()
    return row_id


# ---------- verification ----------

async def verify_chain(conn: AsyncConnection, *, limit: int | None = None) -> dict[str, Any]:
    """Walk the chain in insertion order. Returns a summary dict:
    {ok: bool, checked: int, first_bad: str | None, reason: str | None}.

    Called by scripts/verify_audit_chain.py and by the (future) nightly
    verifier task.
    """
    q = "SELECT id, at, actor_id, actor_type, action, target_type, target_id, " \
        "ip_address, user_agent, request_id, outcome, severity, context_json, " \
        "prev_hash, hash FROM audit_events ORDER BY at, id"
    if limit:
        q += f" LIMIT {int(limit)}"
    result = await conn.execute(text(q))
    rows = result.fetchall()

    expected_prev = GENESIS_HASH
    for r in rows:
        if r.prev_hash != expected_prev:
            return {
                "ok": False, "checked": 0, "first_bad": str(r.id),
                "reason": f"prev_hash mismatch: expected {expected_prev[:12]}…, got {r.prev_hash[:12]}…",
            }
        row_dict = {
            "id": str(r.id),
            "actor_id": str(r.actor_id) if r.actor_id else None,
            "actor_type": r.actor_type,
            "action": r.action,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "ip_address": r.ip_address,
            "user_agent": r.user_agent,
            "request_id": r.request_id,
            "outcome": r.outcome,
            "severity": r.severity,
            "context_json": r.context_json or {},
        }
        recomputed = _compute_hash(r.prev_hash, row_dict)
        if recomputed != r.hash:
            return {
                "ok": False, "checked": 0, "first_bad": str(r.id),
                "reason": f"hash mismatch on row {r.id}: recomputed {recomputed[:12]}…, stored {r.hash[:12]}…",
            }
        expected_prev = r.hash

    return {"ok": True, "checked": len(rows), "first_bad": None, "reason": None}
