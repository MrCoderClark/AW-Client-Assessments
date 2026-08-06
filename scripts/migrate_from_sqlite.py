"""One-shot migration: copy pdfs / pc_status / scan_runs / schedule rows
from data.db (SQLite) into clientfiles_v2 (Postgres).

Idempotent by natural key. Safe to rerun — will skip rows already present.

Run:  uv run --env-file .env python scripts/migrate_from_sqlite.py
Options:
  --sqlite <path>   default: ./data.db
  --dry-run         count what would be inserted, don't write
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import connect as pg_connect


def _to_bool(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    return bool(int(v))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default="data.db")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src_path = Path(args.sqlite)
    if not src_path.exists():
        sys.exit(f"[fail] SQLite file not found: {src_path.resolve()}")
    print(f"[src] {src_path.resolve()}")

    src = sqlite3.connect(src_path)
    src.row_factory = sqlite3.Row

    dst = pg_connect()

    # ---- pdfs ----
    src_pdfs = src.execute(
        "SELECT host, source_path, filename, proposed_name, assessment_type, "
        "       first_name, last_name, size, mtime, md5, indexed_at, committed_at, dest_path "
        "FROM pdfs"
    ).fetchall()
    if args.dry_run:
        print(f"[dry] pdfs: would consider {len(src_pdfs)} rows")
    else:
        n_new = n_skip = 0
        for r in src_pdfs:
            done = dst.execute(
                """
                INSERT INTO pdfs (
                    host, source_path, filename, proposed_name, assessment_type,
                    first_name, last_name, size, mtime, md5,
                    indexed_at, committed_at, dest_path
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (host, source_path) DO NOTHING
                """,
                (
                    r["host"], r["source_path"], r["filename"], r["proposed_name"], r["assessment_type"],
                    r["first_name"], r["last_name"], r["size"], r["mtime"], r["md5"],
                    r["indexed_at"], r["committed_at"], r["dest_path"],
                ),
            )
            if done.rowcount:
                n_new += 1
            else:
                n_skip += 1
        dst.commit()
        print(f"[pdfs] new={n_new} skip={n_skip} total_src={len(src_pdfs)}")

    # ---- pc_status ----
    src_pcs = src.execute("SELECT * FROM pc_status").fetchall()
    if args.dry_run:
        print(f"[dry] pc_status: would upsert {len(src_pcs)} rows")
    else:
        for r in src_pcs:
            dst.execute(
                """
                INSERT INTO pc_status (pc_name, host, last_attempt, last_seen,
                                       last_reachable, last_error, last_counts_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (pc_name) DO UPDATE SET
                    host = EXCLUDED.host,
                    last_attempt = EXCLUDED.last_attempt,
                    last_seen = EXCLUDED.last_seen,
                    last_reachable = EXCLUDED.last_reachable,
                    last_error = EXCLUDED.last_error,
                    last_counts_json = EXCLUDED.last_counts_json
                """,
                (
                    r["pc_name"], r["host"], r["last_attempt"], r["last_seen"],
                    _to_bool(r["last_reachable"]),
                    r["last_error"], r["last_counts_json"],
                ),
            )
        dst.commit()
        print(f"[pc_status] upserted {len(src_pcs)}")

    # ---- scan_runs ----
    dst_runs_count = dst.execute("SELECT COUNT(*) AS n FROM scan_runs").fetchone()["n"]
    src_runs = src.execute(
        "SELECT id, mode, started_at, ended_at, counts_json, error FROM scan_runs"
    ).fetchall()
    if args.dry_run:
        print(f"[dry] scan_runs: {len(src_runs)} in src, {dst_runs_count} already in dst")
    elif dst_runs_count > 0:
        print(f"[scan_runs] dst already has {dst_runs_count} rows; skipping (rerun after truncate to redo)")
    else:
        for r in src_runs:
            dst.execute(
                """
                INSERT INTO scan_runs (mode, started_at, ended_at, counts_json, error)
                VALUES (%s, %s, %s, %s::jsonb, %s)
                """,
                (r["mode"], r["started_at"], r["ended_at"], r["counts_json"], r["error"]),
            )
        dst.commit()
        print(f"[scan_runs] inserted {len(src_runs)} (ids reassigned)")

    # ---- schedule (singleton row 1) ----
    sched = src.execute("SELECT * FROM schedule WHERE id = 1").fetchone()
    if sched is None:
        print("[schedule] no source row; leaving Postgres default")
    elif args.dry_run:
        print(f"[dry] schedule: would overwrite with {dict(sched)}")
    else:
        dst.execute(
            """
            UPDATE schedule
            SET enabled = %s, mode = %s, time_of_day = %s, weekdays = %s,
                last_run_at = %s, last_run_ok = %s, email_on_commit = %s
            WHERE id = 1
            """,
            (
                _to_bool(sched["enabled"]),
                sched["mode"], sched["time_of_day"], sched["weekdays"],
                sched["last_run_at"], _to_bool(sched["last_run_ok"]),
                _to_bool(sched["email_on_commit"]),
            ),
        )
        dst.commit()
        print("[schedule] copied")

    src.close()
    dst.close()
    print("[done]")


if __name__ == "__main__":
    main()
