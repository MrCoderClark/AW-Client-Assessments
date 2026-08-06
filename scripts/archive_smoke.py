r"""Archive + restore smoke.

Verifies:
  1. Explicit-id archive round-trip: rename to _Archive/, DB columns set,
     PDF_ARCHIVED audit row emitted (per file) + PDF_BULK_ARCHIVE (per op).
  2. Explicit-id restore round-trip: rename back, DB columns cleared,
     PDF_RESTORED + PDF_BULK_RESTORE emitted.
  3. Idempotency: re-run archive on already-archived rows → all "skipped"
     (no DB churn, no file movement). Same for restore on active rows.
  4. Streaming date-range archive/restore drains cleanly on an empty set.

Requires:
  - Postgres reachable via DATABASE_URL
  - Destination share reachable via DEST_SMB_USER/DEST_SMB_PASS
  - At least N committed, non-archived rows in `pdfs` whose dest_path
    is under the standard \\host\share\MM-DD-YYYY\ shape.

Run:
  uv run --env-file .env python scripts/archive_smoke.py            # picks 3 rows
  uv run --env-file .env python scripts/archive_smoke.py --ids 42,43,44
  uv run --env-file .env python scripts/archive_smoke.py --n 5
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import smbclient  # noqa: E402

from archive import (  # noqa: E402
    archive_by_date_stream,
    archive_ids,
    register_job,
    restore_by_date_stream,
    restore_ids,
)
from db import connect  # noqa: E402


_DEST_PATH_RE = re.compile(
    r"^\\\\[^\\]+\\[^\\]+\\\d{2}-\d{2}-\d{4}\\[^\\]+$"
)


def _pick_ids(n: int) -> list[int]:
    """Find `n` committed rows whose dest_path looks like a dated-folder path."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, dest_path FROM pdfs "
            "WHERE committed_at IS NOT NULL AND archived_at IS NULL "
            "  AND dest_path IS NOT NULL "
            "ORDER BY id DESC LIMIT 200",
        ).fetchall()
    finally:
        conn.close()
    matched = [r["id"] for r in rows if _DEST_PATH_RE.match(r["dest_path"] or "")]
    if len(matched) < n:
        raise SystemExit(
            f"Need {n} committed rows with dated-folder dest_path — found {len(matched)}."
        )
    return matched[:n]


def _row_state(pdf_id: int) -> dict:
    conn = connect()
    try:
        r = conn.execute(
            "SELECT id, dest_path, archive_path, archived_at, archive_status "
            "FROM pdfs WHERE id = %s", (pdf_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(r) if r else {}


def _smb_exists(path: str) -> bool:
    try:
        smbclient.stat(path)
        return True
    except OSError:
        return False


def _audit_count(action: str, target_id: str | None = None,
                 since: datetime | None = None) -> int:
    conn = connect()
    try:
        clauses = ["action = %s"]
        params: list = [action]
        if target_id is not None:
            clauses.append("target_id = %s")
            params.append(target_id)
        if since is not None:
            clauses.append("at >= %s")
            params.append(since)
        r = conn.execute(
            f"SELECT COUNT(*) AS n FROM audit_events WHERE {' AND '.join(clauses)}",
            tuple(params),
        ).fetchone()
    finally:
        conn.close()
    return int(r["n"]) if r else 0


def _fail(msg: str) -> None:
    print(f"  ✗ {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="", help="Comma-separated pdf ids to use")
    ap.add_argument("--n", type=int, default=3,
                    help="How many rows to pick if --ids not given (default 3)")
    args = ap.parse_args()

    if args.ids:
        ids = [int(x) for x in args.ids.split(",") if x.strip()]
    else:
        ids = _pick_ids(args.n)
    print(f"[setup] using {len(ids)} row id(s): {ids}")

    # Snapshot pre-archive state
    pre = {i: _row_state(i) for i in ids}
    for i, s in pre.items():
        if s["archived_at"] is not None:
            _fail(f"row {i} already archived — clean up first")
        if not s["dest_path"] or not _smb_exists(s["dest_path"]):
            _fail(f"row {i} dest_path missing or unreadable: {s['dest_path']}")

    t0 = datetime.now(timezone.utc) - timedelta(seconds=5)

    # ---- Archive ----
    print("[archive] running archive_ids…")
    res = archive_ids(ids)
    if res["ok"] != len(ids):
        _fail(f"archive_ids ok={res['ok']} expected {len(ids)} — errors: {res['errors']}")
    _ok(f"archive_ids ok={res['ok']} skipped={res['skipped']} failed={res['failed']}")

    for i in ids:
        s = _row_state(i)
        if s["archived_at"] is None:
            _fail(f"row {i} archived_at is still NULL post-archive")
        if not s["archive_path"]:
            _fail(f"row {i} archive_path is empty post-archive")
        if s["archive_status"] != "archived":
            _fail(f"row {i} archive_status={s['archive_status']!r} (want 'archived')")
        if not _smb_exists(s["archive_path"]):
            _fail(f"row {i} archive_path file missing: {s['archive_path']}")
        if _smb_exists(pre[i]["dest_path"]):
            _fail(f"row {i} original dest_path still exists: {pre[i]['dest_path']}")
        n = _audit_count("PDF_ARCHIVED", target_id=str(i), since=t0)
        if n < 1:
            _fail(f"row {i} PDF_ARCHIVED audit row missing")
    _ok("all rows have archive_path/archived_at/archive_status set")
    _ok("all files present at archive_path, absent from dest_path")
    _ok("per-file PDF_ARCHIVED audit rows found")
    if _audit_count("PDF_BULK_ARCHIVE", since=t0) < 1:
        _fail("PDF_BULK_ARCHIVE audit row missing")
    _ok("PDF_BULK_ARCHIVE audit row found")

    # ---- Idempotent re-archive ----
    print("[archive] re-running (should be no-op)…")
    res2 = archive_ids(ids)
    # rows are now filtered out by "archived_at IS NULL" — empty rowset,
    # so ok/skipped/failed all zero.
    if res2["ok"] or res2["failed"]:
        _fail(f"idempotent re-archive should be no-op: {res2}")
    _ok("re-archive is a no-op (as expected)")

    # ---- Restore ----
    t1 = datetime.now(timezone.utc)
    print("[restore] running restore_ids…")
    res3 = restore_ids(ids)
    if res3["ok"] != len(ids):
        _fail(f"restore_ids ok={res3['ok']} expected {len(ids)} — errors: {res3['errors']}")
    _ok(f"restore_ids ok={res3['ok']}")

    for i in ids:
        s = _row_state(i)
        if s["archived_at"] is not None:
            _fail(f"row {i} archived_at not cleared post-restore")
        if s["archive_path"]:
            _fail(f"row {i} archive_path not cleared post-restore")
        if s["archive_status"] is not None:
            _fail(f"row {i} archive_status not cleared post-restore")
        if not _smb_exists(s["dest_path"]):
            _fail(f"row {i} dest_path missing after restore: {s['dest_path']}")
        n = _audit_count("PDF_RESTORED", target_id=str(i), since=t1)
        if n < 1:
            _fail(f"row {i} PDF_RESTORED audit row missing")
    _ok("all rows back to active state, files at dest_path")
    _ok("per-file PDF_RESTORED audit rows found")
    if _audit_count("PDF_BULK_RESTORE", since=t1) < 1:
        _fail("PDF_BULK_RESTORE audit row missing")
    _ok("PDF_BULK_RESTORE audit row found")

    # ---- Idempotent re-restore ----
    print("[restore] re-running (should be no-op)…")
    res4 = restore_ids(ids)
    if res4["ok"] or res4["failed"]:
        _fail(f"idempotent re-restore should be no-op: {res4}")
    _ok("re-restore is a no-op (as expected)")

    # ---- Streaming empty-set drain ----
    print("[stream] draining empty archive-by-date (cutoff in the far past)…")
    long_ago = datetime(1970, 1, 1, tzinfo=timezone.utc)
    job_id = register_job("archive", before=long_ago, after=None, actor_id=None)
    frames = list(archive_by_date_stream(before=long_ago, job_id=job_id))
    kinds = [f["phase"] for f in frames]
    if kinds != ["start", "done"] or frames[-1]["total"] != 0:
        _fail(f"empty-set archive stream produced unexpected frames: {kinds}")
    _ok("archive-by-date stream drains cleanly on empty set")

    job_id = register_job("restore", before=long_ago, after=None, actor_id=None)
    frames = list(restore_by_date_stream(before=long_ago, job_id=job_id))
    kinds = [f["phase"] for f in frames]
    if kinds != ["start", "done"] or frames[-1]["total"] != 0:
        _fail(f"empty-set restore stream produced unexpected frames: {kinds}")
    _ok("restore-by-date stream drains cleanly on empty set")

    print()
    print("[done] archive smoke passed")


if __name__ == "__main__":
    main()
