r"""Copy → verify → delete every uncommitted row from the index.

Reads from data.db WHERE committed_at IS NULL. For each row:
  - If another committed row has the same md5, treat as duplicate:
      delete source, mark committed with dest_path pointing at the original.
  - Else copy to \\<dest-share>\<MM-DD-YYYY>\<proposed_name>,
      handle name collisions with _1, _2, ...,
      verify size, delete source, mark committed.

Callable as CLI (uv run commit.py) or as a generator from api.py.
"""
import os
import sys
import time
from datetime import date
from typing import Iterator

import smbclient

from db import connect, finish_run, get_schedule, start_run
from email_report import send_commit_report
from unlock import kill_pdf_readers

CHUNK = 1024 * 1024
UNLOCK_WAIT = 1.0
DEFAULT_DEST_SHARE = r"\\192.168.70.10\Client_Assessments"


def _creds() -> tuple[str, str, str, str, str]:
    src_u, src_p = os.environ.get("SMB_USER"), os.environ.get("SMB_PASS")
    dest_u, dest_p = os.environ.get("DEST_SMB_USER"), os.environ.get("DEST_SMB_PASS")
    share = os.environ.get("DEST_SHARE", DEFAULT_DEST_SHARE)
    if not (src_u and src_p and dest_u and dest_p):
        raise RuntimeError("Need SMB_USER, SMB_PASS, DEST_SMB_USER, DEST_SMB_PASS")
    return src_u, src_p, dest_u, dest_p, share


def _dest_host(share: str) -> str:
    return share.lstrip("\\").split("\\", 1)[0]


def _smb_exists(path: str) -> bool:
    try:
        smbclient.stat(path)
        return True
    except OSError:
        return False


def _next_available(dest_folder: str, base_name: str) -> str:
    stem, ext = os.path.splitext(base_name)
    candidate = base_name
    counter = 1
    while _smb_exists(f"{dest_folder}\\{candidate}"):
        candidate = f"{stem}_{counter}{ext}"
        counter += 1
    return candidate


def _copy_with_unlock(src: str, dst: str, src_host: str, src_user: str, src_pass: str) -> str | None:
    def do_copy():
        with smbclient.open_file(src, mode="rb") as fin, smbclient.open_file(dst, mode="wb") as fout:
            while chunk := fin.read(CHUNK):
                fout.write(chunk)

    try:
        do_copy()
        return None
    except Exception as e:
        if "0xc0000043" not in str(e).lower() and "used by another process" not in str(e).lower():
            raise
        killed = kill_pdf_readers(src_host, src_user, src_pass)
        note = f"killed {killed or 'nothing'}"
        time.sleep(UNLOCK_WAIT)
        do_copy()
        return note


def _maybe_email(conn, run_id: int, counts: dict) -> Iterator[str]:
    """Send commit report if the schedule toggle is on. Yields at most one log line."""
    sched = get_schedule(conn)
    if not sched.get("email_on_commit"):
        return
    row = conn.execute("SELECT started_at, ended_at FROM scan_runs WHERE id = %s", (run_id,)).fetchone()
    if not row:
        return
    ok, msg = send_commit_report(run_id, counts, row["started_at"], row["ended_at"] or row["started_at"])
    yield f"  [{'email' if ok else 'email-fail'}] {msg}"


def commit_all(only_ids: list[int] | None = None) -> Iterator[str]:
    """Consume every uncommitted row (or a specific id set), copy → verify → delete.

    Yields log lines. If `only_ids` is provided, restricts the batch to those rows.
    """
    src_u, src_p, dest_u, dest_p, share = _creds()
    conn = connect()
    run_id = start_run(conn, "commit")

    dest_host = _dest_host(share)
    smbclient.register_session(dest_host, username=dest_u, password=dest_p, connection_timeout=10)

    dated_folder = f"{share}\\{date.today():%m-%d-%Y}"
    smbclient.makedirs(dated_folder, exist_ok=True)
    yield f"Destination: {dated_folder}"

    # Pre-register source hosts for uncommitted rows
    for row in conn.execute("SELECT DISTINCT host FROM pdfs WHERE committed_at IS NULL"):
        smbclient.register_session(row["host"], username=src_u, password=src_p, connection_timeout=5)

    base_sql = """
        SELECT id, host, source_path, filename, proposed_name, md5, text_hash
        FROM pdfs
        WHERE committed_at IS NULL AND proposed_name IS NOT NULL
    """
    if only_ids:
        # ponytail: Postgres handles small IN lists directly — 24 PCs × few files fits easily.
        placeholders = ",".join(["%s"] * len(only_ids))
        rows = list(conn.execute(f"{base_sql} AND id IN ({placeholders}) ORDER BY host, source_path", tuple(only_ids)))
        yield f"Committing {len(rows)} of {len(only_ids)} selected (skipping already-committed)…"
    else:
        rows = list(conn.execute(f"{base_sql} ORDER BY host, source_path"))

    if not rows:
        empty_counts = {"copied": 0, "duplicates": 0, "failed": 0, "eligible": 0}
        finish_run(conn, run_id, empty_counts)
        yield "Nothing to commit."
        for line in _maybe_email(conn, run_id, empty_counts):
            yield line
        return

    n_copied = n_dup = n_failed = 0

    for row in rows:
        # ponytail: dedupe on byte-identical (md5) OR content-identical
        # (text_hash). The vendor embeds a fresh /CreationDate on every
        # download so md5 alone misses the true duplicate.
        # Archived rows are excluded from dedupe: a re-scanned copy of a
        # previously archived file must land in the current day's folder,
        # not be silently deleted as a duplicate of the archived original.
        prior = conn.execute(
            """
            SELECT dest_path FROM pdfs
            WHERE (md5 = %s OR (text_hash IS NOT NULL AND text_hash = %s))
              AND committed_at IS NOT NULL
              AND archived_at IS NULL
              AND dest_path IS NOT NULL
            LIMIT 1
            """,
            (row["md5"], row["text_hash"]),
        ).fetchone()

        if prior:
            try:
                smbclient.remove(row["source_path"])
                conn.execute(
                    "UPDATE pdfs SET committed_at = NOW(), dest_path = %s WHERE id = %s",
                    (prior["dest_path"], row["id"]),
                )
                conn.commit()
                n_dup += 1
                yield f"  [dup] {row['host']} {row['filename']} — source deleted, dest already at {prior['dest_path']}"
            except Exception as e:
                n_failed += 1
                yield f"  [dup-delete-fail] {row['filename']}: {e}"
            continue

        dest_name = _next_available(dated_folder, row["proposed_name"])
        dest_full = f"{dated_folder}\\{dest_name}"

        try:
            unlock_note = _copy_with_unlock(row["source_path"], dest_full, row["host"], src_u, src_p)
            if unlock_note:
                yield f"    [unlock] {row['host']}: {unlock_note} — retried copy"

            src_stat = smbclient.stat(row["source_path"])
            dst_stat = smbclient.stat(dest_full)
            if src_stat.st_size != dst_stat.st_size:
                n_failed += 1
                yield f"  [verify-fail] {row['filename']}: size mismatch ({src_stat.st_size} != {dst_stat.st_size}); leaving source"
                continue

            smbclient.remove(row["source_path"])
            conn.execute(
                "UPDATE pdfs SET committed_at = NOW(), dest_path = %s WHERE id = %s",
                (dest_full, row["id"]),
            )
            conn.commit()
            n_copied += 1
            yield f"  [ok]  {row['host']}  {row['filename']}  →  {dest_name}"
        except Exception as e:
            n_failed += 1
            yield f"  [fail] {row['filename']}: {e.__class__.__name__}: {e}"

    final_counts = {"copied": n_copied, "duplicates": n_dup, "failed": n_failed, "eligible": len(rows)}
    finish_run(conn, run_id, final_counts)
    yield f"copied={n_copied}  duplicates={n_dup}  failed={n_failed}"
    for line in _maybe_email(conn, run_id, final_counts):
        yield line


def main() -> None:
    for line in commit_all():
        print(line)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit(str(e))
