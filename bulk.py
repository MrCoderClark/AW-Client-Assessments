"""Bulk row actions dispatched from the UI's action bar.

Currently: delete_rows. Commit-selected reuses commit.commit_all(only_ids=...).
"""
import os
from typing import Iterator

import smbclient

from db import connect


def _dest_host(share: str) -> str:
    return share.lstrip("\\").split("\\", 1)[0]


def _register_for(host: str) -> None:
    share = os.environ.get("DEST_SHARE", r"\\192.168.70.10\Client_Assessments")
    if host == _dest_host(share):
        u, p = os.environ.get("DEST_SMB_USER"), os.environ.get("DEST_SMB_PASS")
        timeout = 10
    else:
        u, p = os.environ.get("SMB_USER"), os.environ.get("SMB_PASS")
        timeout = 5
    if not (u and p):
        raise RuntimeError(f"missing SMB creds for host {host}")
    smbclient.register_session(host, username=u, password=p, connection_timeout=timeout)


def delete_rows(ids: list[int], delete_files: bool = False) -> Iterator[str]:
    """Remove DB rows for the given ids. Optionally delete the on-disk file too.

    File choice: dest_path if committed, source_path otherwise. Missing files log a warning.
    """
    if not ids:
        yield "Nothing to delete."
        return

    conn = connect()
    placeholders = ",".join(["%s"] * len(ids))
    rows = list(conn.execute(
        f"SELECT id, host, source_path, dest_path, committed_at, filename FROM pdfs WHERE id IN ({placeholders})",
        tuple(ids),
    ))
    if not rows:
        yield "No matching rows."
        return

    yield f"Deleting {len(rows)} row(s){' + files' if delete_files else ''}…"

    n_row = n_file = n_missing = n_fail = 0
    for r in rows:
        target = r["dest_path"] if r["committed_at"] else r["source_path"]
        if delete_files and target:
            try:
                host = target.lstrip("\\").split("\\", 1)[0]
                _register_for(host)
                smbclient.remove(target)
                n_file += 1
                yield f"  [file-del] {target}"
            except OSError as e:
                if "not found" in str(e).lower() or "cannot find" in str(e).lower():
                    n_missing += 1
                    yield f"  [file-missing] {target}"
                else:
                    n_fail += 1
                    yield f"  [file-fail] {r['filename']}: {e.__class__.__name__}: {e}"
            except Exception as e:
                n_fail += 1
                yield f"  [file-fail] {r['filename']}: {e.__class__.__name__}: {e}"
        conn.execute("DELETE FROM pdfs WHERE id = %s", (r["id"],))
        n_row += 1
        yield f"  [row-del] id={r['id']} {r['filename']}"

    conn.commit()
    yield f"rows_deleted={n_row}  files_deleted={n_file}  missing={n_missing}  failed={n_fail}"
