"""Postgres index of every parseable PDF found on the client PCs, plus per-PC
status and scan-run history.

Migrated from SQLite (data.db) to the same Postgres DB that holds auth.
Schema is owned by Alembic; this module only reads/writes rows.

Public API is unchanged from the SQLite version — callers do not need to
know we switched drivers.
"""
import json
import os
import re
from typing import Any

import psycopg
from psycopg.rows import dict_row


def _sync_dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    # DATABASE_URL uses the async SA driver prefix (postgresql+asyncpg://…);
    # strip it for psycopg. ponytail: single env var for both drivers.
    return re.sub(r"^postgresql\+[a-z]+", "postgresql", url)


APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "America/New_York")


def connect() -> psycopg.Connection:
    """A configured psycopg connection with dict-row semantics.

    Callers use `conn.execute(sql, params).fetchone()` / `.fetchall()` /
    iteration — same shape as the old sqlite3 code. Rows are dicts (drop-in
    for `sqlite3.Row`) via the dict_row factory.

    Session timezone is pinned so TIMESTAMPTZ values always come back in the
    app's zone regardless of Postgres server / role defaults.
    """
    conn = psycopg.connect(_sync_dsn(), row_factory=dict_row, autocommit=False)
    with conn.cursor() as cur:
        cur.execute(f"SET TIME ZONE '{APP_TIMEZONE}'")
    conn.commit()
    return conn


def existing_fingerprint(conn, host: str, source_path: str) -> tuple[int, str] | None:
    """Return (size, mtime_iso) previously seen for this file, or None."""
    row = conn.execute(
        "SELECT size, mtime FROM pdfs WHERE host = %s AND source_path = %s",
        (host, source_path),
    ).fetchone()
    if not row:
        return None
    mtime = row["mtime"]
    return (row["size"], mtime.isoformat(timespec="seconds") if hasattr(mtime, "isoformat") else mtime)


def content_duplicate_exists(conn, host: str, source_path: str, md5: str, text_hash: str | None) -> bool:
    """True if another *non-archived* row already holds this content.

    Matches byte-identical (md5) OR content-identical (normalized text_hash —
    the vendor stamps a fresh /CreationDate per download, so md5 alone misses
    true dupes). The file's own row (same host+source_path) is excluded so a
    plain re-scan isn't seen as its own duplicate. Archived rows are excluded
    so a re-scanned copy of an archived file can still re-commit to today's
    folder — mirrors the dedupe scope in commit.py.
    """
    row = conn.execute(
        """
        SELECT 1 FROM pdfs
        WHERE archived_at IS NULL
          AND (md5 = %s OR (text_hash IS NOT NULL AND text_hash = %s))
          AND NOT (host = %s AND source_path = %s)
        LIMIT 1
        """,
        (md5, text_hash, host, source_path),
    ).fetchone()
    return row is not None


def sf_name_key(first: str | None, last: str | None) -> str:
    """Normalized 'last|first' key for prefilling a case number from a name."""
    return f"{(last or '').strip().lower()}|{(first or '').strip().lower()}"


def sf_map_get_by_case(conn, case_number: str) -> dict | None:
    return conn.execute(
        "SELECT * FROM sf_client_map WHERE case_number = %s", (case_number,)
    ).fetchone()


def sf_map_get_by_name(conn, name_key: str) -> dict | None:
    """Return the single mapping for this name_key, or None when absent/ambiguous.

    If two different clients ever share a name_key we return None (ambiguous) so
    the rep is forced to confirm rather than risk the wrong record.
    """
    rows = conn.execute(
        "SELECT * FROM sf_client_map WHERE name_key = %s LIMIT 2", (name_key,)
    ).fetchall()
    return rows[0] if len(rows) == 1 else None


def sf_map_upsert(conn, *, case_number: str, account_id: str, account_name: str | None,
                  first_name: str | None, last_name: str | None, confirmed_by: str | None) -> None:
    conn.execute(
        """
        INSERT INTO sf_client_map
            (case_number, account_id, account_name, name_key, first_name, last_name, confirmed_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (case_number) DO UPDATE SET
            account_id = EXCLUDED.account_id,
            account_name = EXCLUDED.account_name,
            name_key = EXCLUDED.name_key,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            confirmed_by = EXCLUDED.confirmed_by,
            confirmed_at = NOW()
        """,
        (case_number, account_id, account_name, sf_name_key(first_name, last_name),
         first_name, last_name, confirmed_by),
    )
    conn.commit()


def upsert(conn, **fields) -> None:
    """Upsert a discovered PDF row keyed by (host, source_path)."""
    cols = ",".join(fields)
    placeholders = ",".join(["%s"] * len(fields))
    updates = ",".join(f"{k}=EXCLUDED.{k}" for k in fields if k not in ("host", "source_path"))
    conn.execute(
        f"INSERT INTO pdfs ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT (host, source_path) DO UPDATE SET {updates}, indexed_at = NOW()",
        tuple(fields.values()),
    )


# ---------- Scan-run + PC-status helpers ----------

def start_run(conn, mode: str) -> int:
    row = conn.execute(
        "INSERT INTO scan_runs (mode) VALUES (%s) RETURNING id",
        (mode,),
    ).fetchone()
    conn.commit()
    return int(row["id"]) if row else 0


def finish_run(conn, run_id: int, counts: dict[str, Any], error: str | None = None) -> None:
    conn.execute(
        "UPDATE scan_runs SET ended_at = NOW(), counts_json = %s::jsonb, error = %s WHERE id = %s",
        (json.dumps(counts), error, run_id),
    )
    conn.commit()


def upsert_pc_status(
    conn,
    pc_name: str,
    host: str,
    reachable: bool,
    counts: dict[str, Any] | None = None,
    error: str | None = None,
) -> bool | None:
    """Returns the prior `last_reachable` value (True/False), or None if this
    is the first record for this PC. Callers can compare to detect a
    reachable→unreachable (or reverse) transition and notify."""
    prior_row = conn.execute(
        "SELECT last_reachable FROM pc_status WHERE pc_name = %s",
        (pc_name,),
    ).fetchone()
    prior = prior_row["last_reachable"] if prior_row is not None else None
    conn.execute(
        """
        INSERT INTO pc_status (pc_name, host, last_attempt, last_seen, last_reachable, last_error, last_counts_json)
        VALUES (%s, %s, NOW(), CASE WHEN %s THEN NOW() END, %s, %s, %s::jsonb)
        ON CONFLICT (pc_name) DO UPDATE SET
            host = EXCLUDED.host,
            last_attempt = EXCLUDED.last_attempt,
            last_seen = COALESCE(EXCLUDED.last_seen, pc_status.last_seen),
            last_reachable = EXCLUDED.last_reachable,
            last_error = EXCLUDED.last_error,
            last_counts_json = EXCLUDED.last_counts_json
        """,
        (
            pc_name, host, reachable, reachable, error,
            json.dumps(counts) if counts else None,
        ),
    )
    conn.commit()
    return prior


# ---------- Schedule (singleton) ----------

def get_schedule(conn) -> dict:
    row = conn.execute("SELECT * FROM schedule WHERE id = 1").fetchone()
    return dict(row) if row else {}


def set_schedule(conn, **fields) -> dict:
    allowed = {"enabled", "mode", "time_of_day", "weekdays", "email_on_commit"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_schedule(conn)
    sets = ", ".join(f"{k} = %s" for k in updates)
    conn.execute(f"UPDATE schedule SET {sets} WHERE id = 1", tuple(updates.values()))
    conn.commit()
    return get_schedule(conn)


def mark_schedule_run(conn, ok: bool) -> None:
    conn.execute(
        "UPDATE schedule SET last_run_at = NOW(), last_run_ok = %s WHERE id = 1",
        (ok,),
    )
    conn.commit()


if __name__ == "__main__":
    conn = connect()
    n = conn.execute("SELECT COUNT(*) AS n FROM pdfs").fetchone()["n"]
    runs = conn.execute("SELECT COUNT(*) AS n FROM scan_runs").fetchone()["n"]
    pcs = conn.execute("SELECT COUNT(*) AS n FROM pc_status").fetchone()["n"]
    print(f"clientfiles_v2 ready. pdfs={n} runs={runs} pcs={pcs}")
    print(f"schedule: {get_schedule(conn)}")
