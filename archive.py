r"""Archive + restore service for committed PDFs.

Two directions, four entry points:

- archive_ids(ids)                 explicit id set  (single file / selection)
- restore_ids(ids)                 explicit id set
- archive_by_date_stream(before)   date-range bulk, SSE-friendly generator
- restore_by_date_stream(before)   date-range bulk, SSE-friendly generator

All four route per-file work through _archive_one / _restore_one so the
retry, dedupe, and audit story is identical regardless of trigger. Each
per-file operation runs in its own committed transaction — no long-held
row locks and re-running after a partial failure is safe (both directions
are idempotent on rerun because the WHERE clause on the row UPDATE
requires the "not yet done" state).

Scale target (docs/ARCHIVING_PLAN.md D6): 50,000+ files in one bulk-by-
date op without OOM, deadlocks, or timeouts. Keyset pagination in batches
of 500, ThreadPoolExecutor (4 workers) for the SMB rename, retry on SMB
credit exhaustion, per-row committed tx.
"""
from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Iterator

import smbclient

from auth import audit as audit_mod
from auth.notifications import NotificationEvent, emit_sync as notify_sync
from db import connect
from share import ARCHIVE_FOLDER_NAME, smb_rename_with_retry

DEFAULT_DEST_SHARE = r"\\192.168.70.10\Client_Assessments"
# ponytail: 100 keeps progress frames arriving every ~2s instead of ~12s
# (SMB rename ≈ 20ms/file). Fine for the DB path — keyset pagination is
# unchanged, just more SELECTs at 4µs each.
BATCH_SIZE = 100
DEFAULT_WORKERS = 4
CONSECUTIVE_FAIL_LIMIT = 5  # auto-pause after this many failures in a row


# ---------- in-memory job registry (cancel + status) -----------------
#
# One dict per running bulk op. `cancelled_at` is checked between batches.
# ponytail: single-process app, so a plain dict + a per-process id is fine.
# Upgrade path: back this with a `bulk_ops` table if we need cross-process
# visibility or crash recovery.

_JOBS: dict[str, dict] = {}


def register_job(kind: str, *, before: datetime | None, after: datetime | None,
                 actor_id: str | None) -> str:
    """Reserve a job id; caller uses it as SSE stream id + cancel handle."""
    from auth.random import new_id
    job_id = new_id()
    _JOBS[job_id] = {
        "id": job_id,
        "kind": kind,
        "before": before,
        "after": after,
        "actor_id": actor_id,
        "started_at": datetime.now(),
        "cancelled_at": None,
        "done": 0,
        "total": None,
    }
    return job_id


def request_cancel(job_id: str) -> bool:
    """Signal cancel. Returns False if the job id isn't known."""
    job = _JOBS.get(job_id)
    if not job:
        return False
    if job["cancelled_at"] is None:
        job["cancelled_at"] = datetime.now()
    return True


def is_cancelled(job_id: str) -> bool:
    job = _JOBS.get(job_id)
    return bool(job and job["cancelled_at"])


def job_status(job_id: str) -> dict | None:
    return _JOBS.get(job_id)


def list_jobs(limit: int = 50) -> list[dict]:
    """Recent jobs, newest first. Returns compact dicts (no bulky context)."""
    jobs = sorted(_JOBS.values(), key=lambda j: j.get("started_at") or datetime.min,
                  reverse=True)
    return [
        {
            "id": j["id"],
            "kind": j["kind"],
            "before": j.get("before").isoformat() if j.get("before") else None,
            "after": j.get("after").isoformat() if j.get("after") else None,
            "started_at": j["started_at"].isoformat() if j.get("started_at") else None,
            "cancelled_at": j["cancelled_at"].isoformat() if j.get("cancelled_at") else None,
            "done": j.get("done") or 0,
            "total": j.get("total"),
        }
        for j in jobs[:limit]
    ]


# ---------- path helpers ---------------------------------------------

# Matches "...\<share_root>\MM-DD-YYYY\<filename>". The MM-DD-YYYY sits
# right below the share root and the filename right below that.
_DEST_PATH_RE = re.compile(
    r"^(?P<root>\\\\[^\\]+\\[^\\]+)\\(?P<date>\d{2}-\d{2}-\d{4})\\(?P<name>[^\\]+)$"
)
_ARCHIVE_PATH_RE = re.compile(
    r"^(?P<root>\\\\[^\\]+\\[^\\]+)\\"
    + re.escape(ARCHIVE_FOLDER_NAME) +
    r"\\(?P<date>\d{2}-\d{2}-\d{4})\\(?P<name>[^\\]+)$"
)


def _dest_to_archive_path(dest_path: str) -> str | None:
    r"""\\host\share\MM-DD-YYYY\foo.pdf → \\host\share\_Archive\MM-DD-YYYY\foo.pdf.

    Returns None if `dest_path` doesn't sit under a dated folder — we
    won't invent an archive path for a legacy row parked somewhere else.
    """
    m = _DEST_PATH_RE.match(dest_path)
    if not m:
        return None
    return f"{m['root']}\\{ARCHIVE_FOLDER_NAME}\\{m['date']}\\{m['name']}"


def _archive_to_dest_path(archive_path: str) -> str | None:
    m = _ARCHIVE_PATH_RE.match(archive_path)
    if not m:
        return None
    return f"{m['root']}\\{m['date']}\\{m['name']}"


# ---------- SMB session setup ----------------------------------------

def _dest_creds() -> tuple[str, str, str]:
    """(user, pass, share_root). Same env vars the commit path uses."""
    u = os.environ.get("DEST_SMB_USER")
    p = os.environ.get("DEST_SMB_PASS")
    share = os.environ.get("DEST_SHARE", DEFAULT_DEST_SHARE)
    if not (u and p):
        raise RuntimeError("Need DEST_SMB_USER and DEST_SMB_PASS in env")
    return u, p, share


def _register_dest_session() -> str:
    """Register the destination share session (process-wide) + return the root."""
    u, p, share = _dest_creds()
    host = share.lstrip("\\").split("\\", 1)[0]
    smbclient.register_session(host, username=u, password=p, connection_timeout=10)
    return share


# ---------- per-file archive + restore -------------------------------

def _archive_one(row: dict, *, actor_id: str | None = None) -> dict:
    """Rename one file → _Archive/ and update its row. Returns a result dict.

    Result kinds:
      ok        — rename + DB update landed
      skip      — row already archived (idempotent no-op)
      no_dest   — row has no dest_path or it doesn't match the dated shape
      not_found — file missing at dest_path (nothing to move)
      fail      — SMB or DB error, message in `err`

    Runs its own committed tx so a mid-batch crash doesn't strand the
    other rows in a giant open transaction.
    """
    rid = row["id"]
    dest = row["dest_path"]
    if not dest:
        return {"id": rid, "kind": "no_dest", "err": "row has no dest_path"}

    archive_path = _dest_to_archive_path(dest)
    if not archive_path:
        return {"id": rid, "kind": "no_dest",
                "err": f"dest_path not under a dated folder: {dest}"}

    # Make sure the target subfolder exists — first archive of the day
    # will be the one that creates _Archive/MM-DD-YYYY/.
    archive_folder = archive_path.rsplit("\\", 1)[0]
    try:
        smbclient.makedirs(archive_folder, exist_ok=True)
    except Exception as e:
        return {"id": rid, "kind": "fail",
                "err": f"makedirs {archive_folder}: {e.__class__.__name__}: {e}"}

    try:
        smb_rename_with_retry(dest, archive_path)
    except OSError as e:
        # File missing at source — surface as its own kind so the caller
        # can flag the row as archive_file_missing rather than as a
        # generic failure.
        if "no such file" in str(e).lower() or "not found" in str(e).lower():
            return {"id": rid, "kind": "not_found",
                    "err": f"file missing at {dest}"}
        return {"id": rid, "kind": "fail",
                "err": f"rename: {e.__class__.__name__}: {e}"}
    except Exception as e:
        return {"id": rid, "kind": "fail",
                "err": f"rename: {e.__class__.__name__}: {e}"}

    # Per-row committed tx. WHERE archived_at IS NULL makes the DB write
    # idempotent — a retry after this succeeds and before the caller
    # reacts won't double-write.
    conn = connect()
    try:
        conn.execute(
            "UPDATE pdfs SET archived_at = now(), archive_path = %s, "
            "archive_status = 'archived' "
            "WHERE id = %s AND archived_at IS NULL",
            (archive_path, rid),
        )
        conn.commit()
    finally:
        conn.close()

    try:
        audit_mod.emit_sync(
            action="PDF_ARCHIVED", actor_id=actor_id,
            actor_type="user" if actor_id else "system",
            target_type="pdf", target_id=str(rid),
            context={"archive_path": archive_path, "prior_dest_path": dest,
                     "filename": row.get("filename")},
        )
    except Exception:
        pass  # per-file audit failure never blocks the file move

    return {"id": rid, "kind": "ok", "archive_path": archive_path,
            "prior_dest_path": dest}


def _restore_one(row: dict, *, actor_id: str | None = None) -> dict:
    """Reverse: rename _Archive/... → original date folder, clear columns."""
    rid = row["id"]
    archive_path = row["archive_path"]
    dest = row["dest_path"]

    if not archive_path:
        return {"id": rid, "kind": "not_archived",
                "err": "row has no archive_path"}

    # Prefer the recorded dest_path (kept unchanged during archive); fall
    # back to reversing the archive_path shape when the row somehow lost
    # its dest_path.
    target = dest or _archive_to_dest_path(archive_path)
    if not target:
        return {"id": rid, "kind": "fail",
                "err": f"can't determine restore target from {archive_path}"}

    target_folder = target.rsplit("\\", 1)[0]
    try:
        smbclient.makedirs(target_folder, exist_ok=True)
    except Exception as e:
        return {"id": rid, "kind": "fail",
                "err": f"makedirs {target_folder}: {e.__class__.__name__}: {e}"}

    # If the original filename is already taken (shouldn't be — nothing
    # ships that reuses archived names — but be defensive), append a
    # stamp and record the actual path used.
    if _smb_exists(target):
        stem, ext = os.path.splitext(target)
        target = f"{stem}.restored-{datetime.now():%Y%m%d-%H%M%S}{ext}"

    try:
        smb_rename_with_retry(archive_path, target)
    except OSError as e:
        if "no such file" in str(e).lower() or "not found" in str(e).lower():
            return {"id": rid, "kind": "archive_missing",
                    "err": f"archive file missing at {archive_path}"}
        return {"id": rid, "kind": "fail",
                "err": f"rename: {e.__class__.__name__}: {e}"}
    except Exception as e:
        return {"id": rid, "kind": "fail",
                "err": f"rename: {e.__class__.__name__}: {e}"}

    conn = connect()
    try:
        conn.execute(
            "UPDATE pdfs SET archived_at = NULL, archive_path = NULL, "
            "archive_status = NULL, dest_path = %s "
            "WHERE id = %s AND archived_at IS NOT NULL",
            (target, rid),
        )
        conn.commit()
    finally:
        conn.close()

    try:
        audit_mod.emit_sync(
            action="PDF_RESTORED", actor_id=actor_id,
            actor_type="user" if actor_id else "system",
            target_type="pdf", target_id=str(rid),
            context={"restored_from": archive_path, "restored_to": target,
                     "filename": row.get("filename")},
        )
    except Exception:
        pass

    return {"id": rid, "kind": "ok",
            "restored_from": archive_path, "restored_to": target}


def _smb_exists(path: str) -> bool:
    try:
        smbclient.stat(path)
        return True
    except OSError:
        return False


# ---------- explicit-id entrypoints ----------------------------------

def archive_ids(ids: list[int], *, actor_id: str | None = None) -> dict:
    """Archive a specific set of rows. Returns counts + per-file errors."""
    if not ids:
        return {"ok": 0, "skipped": 0, "failed": 0, "errors": []}

    _register_dest_session()
    conn = connect()
    try:
        placeholders = ",".join(["%s"] * len(ids))
        rows = list(conn.execute(
            f"SELECT id, dest_path, filename FROM pdfs "
            f"WHERE id IN ({placeholders}) AND archived_at IS NULL",
            tuple(ids),
        ))
    finally:
        conn.close()

    return _run_batch(rows, direction="archive", actor_id=actor_id,
                      job_id=None)


def restore_ids(ids: list[int], *, actor_id: str | None = None) -> dict:
    if not ids:
        return {"ok": 0, "skipped": 0, "failed": 0, "errors": []}

    _register_dest_session()
    conn = connect()
    try:
        placeholders = ",".join(["%s"] * len(ids))
        rows = list(conn.execute(
            f"SELECT id, dest_path, archive_path, filename FROM pdfs "
            f"WHERE id IN ({placeholders}) AND archived_at IS NOT NULL",
            tuple(ids),
        ))
    finally:
        conn.close()

    return _run_batch(rows, direction="restore", actor_id=actor_id,
                      job_id=None)


# ---------- streaming date-range entrypoints -------------------------

def archive_by_date_stream(
    *,
    before: datetime,
    after: datetime | None = None,
    actor_id: str | None = None,
    job_id: str,
    workers: int = DEFAULT_WORKERS,
) -> Iterator[dict]:
    """Keyset-paginate archivable rows and yield SSE-friendly frames.

    A frame is a plain dict; callers wrap it as `data: json\\n\\n`. Frames:
      {"phase": "start",     "total": T}
      {"phase": "progress",  "done": D, "total": T, "batch_ms": X,
                              "batch_ok": B, "batch_fail": F,
                              "errors": [{"id":…, "err":…}, …]}
      {"phase": "paused",    "reason": "..."}     # 5 consecutive failures
      {"phase": "cancelled", "done": D, "total": T}
      {"phase": "done",      "done": D, "total": T,
                              "ok": O, "skipped": S, "failed": F}
    """
    yield from _stream(
        direction="archive",
        before=before, after=after,
        actor_id=actor_id, job_id=job_id, workers=workers,
    )


def restore_by_date_stream(
    *,
    before: datetime,
    after: datetime | None = None,
    date_field: str = "archived_at",
    actor_id: str | None = None,
    job_id: str,
    workers: int = DEFAULT_WORKERS,
) -> Iterator[dict]:
    yield from _stream(
        direction="restore",
        before=before, after=after,
        date_field=date_field,
        actor_id=actor_id, job_id=job_id, workers=workers,
    )


# ---------- shared streaming machinery -------------------------------

def _stream(
    *,
    direction: str,                 # "archive" | "restore"
    before: datetime,
    after: datetime | None,
    date_field: str = "committed_at",
    actor_id: str | None,
    job_id: str,
    workers: int,
) -> Iterator[dict]:
    _register_dest_session()

    total = _count_eligible(direction=direction, before=before, after=after,
                            date_field=date_field)
    _JOBS.setdefault(job_id, {})["total"] = total
    yield {"phase": "start", "total": total}

    if total == 0:
        _emit_bulk_audit(direction, actor_id=actor_id,
                         count=0, before=before, after=after,
                         cancelled=False)
        yield {"phase": "done", "done": 0, "total": 0,
               "ok": 0, "skipped": 0, "failed": 0}
        return

    n_ok = n_fail = n_skip = 0
    consecutive_fail = 0
    last_id = 0
    done = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            if is_cancelled(job_id):
                yield {"phase": "cancelled", "done": done, "total": total}
                break

            rows = _next_batch(
                direction=direction, before=before, after=after,
                date_field=date_field, last_id=last_id,
            )
            if not rows:
                break

            t0 = time.perf_counter()
            worker = _archive_one if direction == "archive" else _restore_one
            futures = [pool.submit(worker, r, actor_id=actor_id) for r in rows]
            batch_errors: list[dict] = []
            batch_ok = batch_fail = 0
            for fut in as_completed(futures):
                res = fut.result()
                kind = res["kind"]
                if kind == "ok":
                    n_ok += 1
                    batch_ok += 1
                    consecutive_fail = 0
                elif kind in ("skip", "not_archived"):
                    n_skip += 1
                elif kind in ("not_found", "archive_missing"):
                    n_fail += 1
                    batch_fail += 1
                    consecutive_fail += 1
                    batch_errors.append({"id": res["id"], "err": res["err"]})
                    # Mark the row so a repair pass can pick it up.
                    _mark_missing(res["id"], direction)
                else:  # fail | no_dest
                    n_fail += 1
                    batch_fail += 1
                    consecutive_fail += 1
                    batch_errors.append({"id": res["id"], "err": res["err"]})

            done += len(rows)
            last_id = rows[-1]["id"]
            _JOBS.setdefault(job_id, {})["done"] = done

            yield {
                "phase": "progress",
                "done": done,
                "total": total,
                "batch_ms": int((time.perf_counter() - t0) * 1000),
                "batch_ok": batch_ok,
                "batch_fail": batch_fail,
                "errors": batch_errors[:20],  # cap tail so a bad share doesn't flood
            }

            if consecutive_fail >= CONSECUTIVE_FAIL_LIMIT:
                yield {
                    "phase": "paused",
                    "reason": f"{CONSECUTIVE_FAIL_LIMIT} consecutive failures — "
                              "share may be unreachable",
                    "done": done, "total": total,
                }
                break

    cancelled = is_cancelled(job_id)
    _emit_bulk_audit(
        direction, actor_id=actor_id,
        count=n_ok, before=before, after=after, cancelled=cancelled,
    )
    _emit_bulk_notification(
        direction, count=n_ok, failed=n_fail, cancelled=cancelled,
    )
    yield {
        "phase": "done", "done": done, "total": total,
        "ok": n_ok, "skipped": n_skip, "failed": n_fail,
    }


# ---------- SQL helpers ---------------------------------------------

def _where_and_params(direction: str, before: datetime, after: datetime | None,
                      date_field: str) -> tuple[str, list]:
    if direction == "archive":
        clauses = ["committed_at < %s", "archived_at IS NULL"]
        params: list = [before]
        if after is not None:
            clauses.insert(0, "committed_at >= %s")
            params.insert(0, after)
    else:  # restore
        col = date_field if date_field in ("archived_at", "committed_at") else "archived_at"
        clauses = [f"{col} < %s", "archived_at IS NOT NULL",
                   "archive_status = 'archived'"]
        params = [before]
        if after is not None:
            clauses.insert(0, f"{col} >= %s")
            params.insert(0, after)
    return " AND ".join(clauses), params


def _count_eligible(*, direction: str, before: datetime,
                    after: datetime | None, date_field: str) -> int:
    where, params = _where_and_params(direction, before, after, date_field)
    conn = connect()
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM pdfs WHERE {where}", tuple(params),
        ).fetchone()
        return int(row["n"]) if row else 0
    finally:
        conn.close()


def _next_batch(*, direction: str, before: datetime,
                after: datetime | None, date_field: str,
                last_id: int, size: int = BATCH_SIZE) -> list[dict]:
    where, params = _where_and_params(direction, before, after, date_field)
    cols = ("id, dest_path, filename"
            if direction == "archive"
            else "id, dest_path, archive_path, filename")
    conn = connect()
    try:
        return list(conn.execute(
            f"SELECT {cols} FROM pdfs "
            f"WHERE {where} AND id > %s "
            f"ORDER BY id LIMIT %s",
            tuple(params) + (last_id, size),
        ))
    finally:
        conn.close()


def _run_batch(rows: list[dict], *, direction: str,
               actor_id: str | None, job_id: str | None) -> dict:
    """Explicit-id helper — run the worker over `rows` synchronously in
    parallel and aggregate results. Used by archive_ids/restore_ids."""
    n_ok = n_fail = n_skip = 0
    errors: list[dict] = []
    worker = _archive_one if direction == "archive" else _restore_one
    with ThreadPoolExecutor(max_workers=DEFAULT_WORKERS) as pool:
        for fut in as_completed(pool.submit(worker, r, actor_id=actor_id) for r in rows):
            res = fut.result()
            kind = res["kind"]
            if kind == "ok":
                n_ok += 1
            elif kind in ("skip", "not_archived"):
                n_skip += 1
            else:
                n_fail += 1
                errors.append({"id": res["id"], "err": res["err"]})
                if kind in ("not_found", "archive_missing"):
                    _mark_missing(res["id"], direction)

    _emit_bulk_audit(direction, actor_id=actor_id,
                     count=n_ok, before=None, after=None, cancelled=False,
                     ids=[r["id"] for r in rows])
    _emit_bulk_notification(direction, count=n_ok, failed=n_fail, cancelled=False)
    return {"ok": n_ok, "skipped": n_skip, "failed": n_fail, "errors": errors}


def _mark_missing(pdf_id: int, direction: str) -> None:
    """Flag a row whose file couldn't be found where the DB expected it.

    Archive direction: the source file at dest_path is gone. The DB row
    is left untouched; audit logs the miss so an operator can decide
    whether to clean up the row.

    Restore direction: the archive file at archive_path is gone. Set
    archive_status='lost' so the repair panel surfaces it, and fire an
    SEC-severity PDF_ARCHIVE_LOST notification (per the plan).
    """
    if direction != "restore":
        try:
            audit_mod.emit_sync(
                action="PDF_ARCHIVE_SOURCE_MISSING",
                severity="WARN",
                target_type="pdf",
                target_id=str(pdf_id),
            )
        except Exception:
            pass
        return

    conn = connect()
    try:
        conn.execute(
            "UPDATE pdfs SET archive_status = 'lost' WHERE id = %s",
            (pdf_id,),
        )
        conn.commit()
    finally:
        conn.close()
    try:
        audit_mod.emit_sync(
            action="PDF_ARCHIVE_LOST",
            severity="SEC",
            target_type="pdf",
            target_id=str(pdf_id),
        )
        notify_sync(NotificationEvent(
            category="scan_commit", kind="pdf_archive_lost", severity="SEC",
            title=f"Archive file missing (pdf id {pdf_id})",
            body="Detected during restore — see the repair panel.",
            url="/admin/archive",
        ))
    except Exception:
        pass


# ---------- audit + notification wrappers ----------------------------

def _emit_bulk_audit(direction: str, *, actor_id: str | None,
                     count: int, before: datetime | None,
                     after: datetime | None, cancelled: bool,
                     ids: list[int] | None = None) -> None:
    action = "PDF_BULK_ARCHIVE" if direction == "archive" else "PDF_BULK_RESTORE"
    ctx: dict = {"count": count, "cancelled": cancelled}
    if before is not None:
        ctx["before"] = before.isoformat()
    if after is not None:
        ctx["after"] = after.isoformat()
    if ids is not None:
        # ponytail: at 24 PCs × handfuls of files a full id list is tiny.
        # Cap at 5000 defensively so we never balloon audit_events with
        # a stray 100k-id blob.
        ctx["ids"] = ids[:5000]
    try:
        audit_mod.emit_sync(
            action=action, actor_id=actor_id,
            actor_type="user" if actor_id else "system",
            outcome="cancelled" if cancelled else "success",
            context=ctx,
        )
    except Exception:
        pass


def _emit_bulk_notification(direction: str, *, count: int,
                            failed: int, cancelled: bool) -> None:
    verb = "archived" if direction == "archive" else "restored"
    title = f"{count} PDFs {verb}"
    if cancelled:
        title += " (cancelled)"
    body_bits = [f"{count} succeeded"]
    if failed:
        body_bits.append(f"{failed} failed")
    body = ", ".join(body_bits)
    severity = "WARN" if failed else "INFO"
    try:
        notify_sync(NotificationEvent(
            category="scan_commit",
            kind=f"pdf_bulk_{direction}",
            severity=severity,
            title=title, body=body,
            url="/admin/archive",
        ))
    except Exception:
        pass
