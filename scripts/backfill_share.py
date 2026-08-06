r"""Backfill: catalog PDFs already sitting on \\192.168.70.10\Client_Assessments.

Walks every MM-DD-YYYY subfolder on the share, reads each PDF, runs the
classifier, and inserts a fully-committed row into the pdfs table. Files
on disk are NEVER modified — no rename, no move, no delete. The rows point
at the file's existing path (source_path = dest_path = share URL).

Dedupe (default): skip if md5 OR text_hash already present in the DB.
That way rerunning the backfill is safe, and files today's live scan
already committed don't get duplicated.

Usage:
  # Preview counts, no writes
  uv run --env-file .env python scripts/backfill_share.py

  # Actually insert rows
  uv run --env-file .env python scripts/backfill_share.py --commit

  # Limit to specific folders (useful for a first-time smoke)
  uv run --env-file .env python scripts/backfill_share.py --folders 08-04-2026,08-03-2026 --commit

  # Different share (defaults to \\192.168.70.10\Client_Assessments)
  uv run --env-file .env python scripts/backfill_share.py --share '\\\\host\\Client_Assessments'
"""
from __future__ import annotations

import argparse
import hashlib
import io
import logging
import os
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Silence pypdf's chatty warnings on malformed PDFs ("incorrect startxref",
# "Multiple definitions in dictionary", etc). None of those stop parsing;
# they just drown the terminal.
logging.getLogger("pypdf").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", module="pypdf")

import smbclient
from pypdf import PdfReader

from classify import detect_assessment, detect_name, proposed_filename
from db import connect
from scan import text_hash
from share import list_active_date_folders


DEFAULT_SHARE = r"\\192.168.70.10\Client_Assessments"
LEGACY_HOST = "legacy"


def _print(*args, **kwargs) -> None:
    """print() with flush=True so PowerShell / redirected output streams live."""
    kwargs.setdefault("flush", True)
    print(*args, **kwargs)


def _creds() -> tuple[str, str]:
    """Reuse the destination share credentials the app already stores."""
    u = os.environ.get("DEST_SMB_USER") or os.environ.get("SMB_USER")
    p = os.environ.get("DEST_SMB_PASS") or os.environ.get("SMB_PASS")
    if not (u and p):
        raise RuntimeError("Set DEST_SMB_USER/DEST_SMB_PASS (or SMB_USER/SMB_PASS) in .env")
    return u, p


def _walk_pdfs(folder: str):
    """Yield (path, name, size, mtime_epoch). stat is captured here on the
    serial walker so worker threads only do the read() SMB call — one op
    instead of two per file. Halves credit pressure on the share."""
    for entry in smbclient.scandir(folder):
        if entry.is_file() and entry.name.lower().endswith(".pdf"):
            st = entry.stat()
            yield f"{folder}\\{entry.name}", entry.name, st.st_size, st.st_mtime


def _existing_hashes(conn) -> tuple[set[str], set[str]]:
    md5s: set[str] = set()
    thashes: set[str] = set()
    for row in conn.execute("SELECT md5, text_hash FROM pdfs"):
        if row["md5"]:
            md5s.add(row["md5"])
        if row["text_hash"]:
            thashes.add(row["text_hash"])
    return md5s, thashes


def _existing_paths(conn) -> set[str]:
    """Every path this app already knows about — legacy source_paths AND
    dest_paths from live commits. Case-normalized for reliable matching.

    Using this set to short-circuit means we never even open the file on the
    share when we already have a row for it. Turns a re-run into a stat-and-skip
    pass instead of a re-hash-everything pass.
    """
    paths: set[str] = set()
    for row in conn.execute(
        "SELECT source_path, dest_path FROM pdfs "
        "WHERE (host = 'legacy' AND source_path IS NOT NULL) OR dest_path IS NOT NULL"
    ):
        if row["source_path"]:
            paths.add(row["source_path"].lower())
        if row["dest_path"]:
            paths.add(row["dest_path"].lower())
    return paths


def _read_pdf_bytes(path: str, max_attempts: int = 5) -> bytes:
    """Read a PDF over SMB with retry on transient credit exhaustion.

    SMB2 uses credit-based flow control; too many concurrent reads on the
    same session can exhaust credits faster than the server grants new ones,
    surfacing as 'Request requires 1 credits but only 0 credits are available'.
    A brief backoff lets credits replenish.
    """
    last_err: Exception | None = None
    for attempt in range(max_attempts):
        try:
            with smbclient.open_file(path, mode="rb") as f:
                return f.read()
        except Exception as e:  # noqa: BLE001
            last_err = e
            msg = str(e).lower()
            transient = ("credits" in msg
                         or "credit" in msg
                         or "STATUS_INSUFFICIENT_RESOURCES" in str(e))
            if not transient or attempt == max_attempts - 1:
                raise
            time.sleep(0.1 * (2 ** attempt))  # 0.1, 0.2, 0.4, 0.8, 1.6
    raise last_err  # unreachable, keeps mypy happy


def _process_one(path: str, name: str, size: int, mtime_epoch: float) -> dict:
    """Worker: read + hash + classify one PDF. No DB, no shared state writes.

    Returns a dict describing what happened. The main thread inspects and
    handles dedupe + insert serially.
    """
    try:
        data = _read_pdf_bytes(path)
    except Exception as e:
        return {"path": path, "name": name, "kind": "unreadable",
                "err": f"{e.__class__.__name__}: {e}"}
    md5 = hashlib.md5(data).hexdigest()
    info = _classify(data, name)
    return {
        "path": path, "name": name, "kind": "ok",
        "md5": md5, "size": size, "mtime_epoch": mtime_epoch,
        "info": info,
    }


def _classify(data: bytes, filename: str) -> dict:
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((p.extract_text() or "") for p in reader.pages[:3])
    except Exception:
        text = ""
    assessment = detect_assessment(text, filename)
    name = detect_name(text, filename)
    first, last = name if name else (None, None)
    new_name = proposed_filename(text, filename) if assessment else None
    return {
        "assessment_type": assessment,
        "first_name": first,
        "last_name": last,
        "proposed_name": new_name,
        "text_hash": text_hash(text),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--share", default=DEFAULT_SHARE,
                    help=f"Share root (default: {DEFAULT_SHARE})")
    ap.add_argument("--folders", default="",
                    help="Comma-separated MM-DD-YYYY folders to process; default = all")
    ap.add_argument("--commit", action="store_true",
                    help="Actually write rows. Without this flag, runs dry.")
    ap.add_argument("--limit", type=int, default=0,
                    help="Stop after N PDFs (0 = unlimited). Handy for smoke.")
    ap.add_argument("--verbose", action="store_true", help="One line per file.")
    ap.add_argument("--parallel", type=int, default=8,
                    help="Worker threads reading PDFs concurrently over SMB (default 8, 1 = serial).")
    ap.add_argument("--progress-every", type=int, default=100,
                    help="Print a rate line every N processed files (default 100).")
    args = ap.parse_args()

    user, password = _creds()
    dest_host = args.share.lstrip("\\").split("\\")[0]
    smbclient.register_session(dest_host, username=user, password=password, connection_timeout=10)

    if args.folders:
        folders = [f.strip() for f in args.folders.split(",") if f.strip()]
    else:
        folders = list_active_date_folders(args.share)
    _print(f"[start] share={args.share}  folders={len(folders)}  mode={'COMMIT' if args.commit else 'dry-run'}")

    conn = connect()
    md5_seen, text_seen = _existing_hashes(conn)
    path_seen = _existing_paths(conn)
    _print(f"[start] db already has {len(md5_seen)} md5s / {len(text_seen)} text_hashes / {len(path_seen)} paths")

    tot = dict(files=0, dup_path=0, inserted=0, dup_md5=0, dup_text=0,
               noassess=0, unreadable=0, skipped_running=0)

    workers = max(1, args.parallel)
    started_at = time.time()

    def _handle(result: dict) -> None:
        """Consume one worker result on the main thread. All DB writes + shared
        set updates happen here so no locks are needed."""
        entry_name = result["name"]
        if result["kind"] == "unreadable":
            tot["unreadable"] += 1
            if args.verbose:
                _print(f"    [unreadable] {entry_name}: {result['err']}")
            return

        md5 = result["md5"]
        info = result["info"]
        path = result["path"]

        if md5 in md5_seen:
            tot["dup_md5"] += 1
            path_seen.add(path.lower())
            if args.verbose:
                _print(f"    [dup-md5] {entry_name}")
            return
        if info["text_hash"] and info["text_hash"] in text_seen:
            tot["dup_text"] += 1
            if args.verbose:
                _print(f"    [dup-text] {entry_name}")
            return
        if not info["assessment_type"]:
            tot["noassess"] += 1
            if args.verbose:
                _print(f"    [noassess] {entry_name}")
            # still catalog with NULL classifier fields

        mtime = datetime.fromtimestamp(result["mtime_epoch"], tz=timezone.utc)
        row = {
            "host": LEGACY_HOST,
            "source_path": path,
            "filename": entry_name,
            "proposed_name": info["proposed_name"],
            "assessment_type": info["assessment_type"],
            "first_name": info["first_name"],
            "last_name": info["last_name"],
            "size": result["size"],
            "mtime": mtime.isoformat(),
            "md5": md5,
            "text_hash": info["text_hash"],
            "committed_at": mtime.isoformat(),
            "dest_path": path,
        }
        if args.commit:
            cols = ",".join(row)
            placeholders = ",".join(["%s"] * len(row))
            updates = ",".join(
                f"{k}=EXCLUDED.{k}" for k in row if k not in ("host", "source_path")
            )
            conn.execute(
                f"INSERT INTO pdfs ({cols}) VALUES ({placeholders}) "
                f"ON CONFLICT (host, source_path) DO UPDATE SET {updates}",
                tuple(row.values()),
            )
            conn.commit()

        md5_seen.add(md5)
        path_seen.add(path.lower())
        if info["text_hash"]:
            text_seen.add(info["text_hash"])
        tot["inserted"] += 1
        if args.verbose:
            _print(f"    [{'insert' if args.commit else 'would-insert'}] "
                   f"{entry_name} -> {info['proposed_name'] or '(no rename)'}")

    for fname_folder in folders:
        folder_path = f"{args.share}\\{fname_folder}"
        try:
            pdfs = list(_walk_pdfs(folder_path))
        except OSError as e:
            _print(f"  [skip-folder] {fname_folder}: {e}")
            continue

        # Fast-path skip everything we already know about — no SMB reads.
        pending: list[tuple[str, str, int, float]] = []
        for path, name, size, mtime_epoch in pdfs:
            tot["files"] += 1
            if path.lower() in path_seen:
                tot["dup_path"] += 1
                if args.verbose:
                    _print(f"    [dup-path] {name}")
                continue
            if args.limit and tot["inserted"] + len(pending) >= args.limit:
                tot["skipped_running"] += 1
                continue
            pending.append((path, name, size, mtime_epoch))

        _print(f"  [{fname_folder}] {len(pdfs)} pdf(s), {len(pending)} to process")

        if not pending:
            continue

        processed_here = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_process_one, p, n, s, m) for p, n, s, m in pending]
            for fut in as_completed(futures):
                _handle(fut.result())
                processed_here += 1
                if processed_here % max(1, args.progress_every) == 0:
                    rate = (tot["inserted"] + tot["dup_md5"] + tot["dup_text"] + tot["unreadable"]) \
                           / max(1, time.time() - started_at)
                    _print(f"    …{processed_here}/{len(pending)} in folder  |  "
                           f"total: {tot['inserted']} ins, {tot['dup_md5']} md5-dup, "
                           f"{tot['unreadable']} bad  |  {rate:.1f} files/s")

    conn.close()

    _print()
    _print(f"[done] mode={'COMMIT' if args.commit else 'dry-run'}")
    _print(f"       files scanned   : {tot['files']}")
    _print(f"       dup on path     : {tot['dup_path']}    (no read — fast path)")
    _print(f"       dup on md5      : {tot['dup_md5']}")
    _print(f"       dup on text_hash: {tot['dup_text']}")
    _print(f"       no assessment   : {tot['noassess']}")
    _print(f"       inserted        : {tot['inserted']}")
    _print(f"       unreadable      : {tot['unreadable']}")
    if args.limit:
        _print(f"       skipped (limit) : {tot['skipped_running']}")
    if not args.commit:
        _print()
        _print("Dry run — no rows written. Re-run with --commit to apply.")


if __name__ == "__main__":
    main()
