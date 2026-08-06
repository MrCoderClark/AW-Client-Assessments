"""Delete on-disk content-duplicate files identified by pdfs.text_hash.

For each cluster (text_hash with >1 committed rows), keeps the
earliest-committed row and deletes the rest — both the file on the
destination share AND the DB row.

Default is a dry-run. Pass --confirm to actually delete. --keep-newest
inverts the keeper choice (defaults to oldest).

Run:
  uv run --env-file .env python scripts/prune_content_dupes.py            # dry
  uv run --env-file .env python scripts/prune_content_dupes.py --confirm  # do it
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import smbclient

from db import connect


def _register_smb(host: str) -> None:
    dest_share = os.environ.get("DEST_SHARE", r"\\192.168.70.10\Client_Assessments")
    dest_host = dest_share.lstrip("\\").split("\\", 1)[0]
    if host == dest_host:
        u, p = os.environ.get("DEST_SMB_USER"), os.environ.get("DEST_SMB_PASS")
        timeout = 10
    else:
        u, p = os.environ.get("SMB_USER"), os.environ.get("SMB_PASS")
        timeout = 5
    if not (u and p):
        raise RuntimeError(f"SMB creds missing for host {host}")
    smbclient.register_session(host, username=u, password=p, connection_timeout=timeout)


def _delete_smb(path: str) -> tuple[bool, str]:
    """Return (deleted, note). Missing file counts as 'gone' (True with note)."""
    try:
        host = path.lstrip("\\").split("\\", 1)[0]
        _register_smb(host)
        smbclient.remove(path)
        return True, "deleted"
    except OSError as e:
        if "not found" in str(e).lower() or "cannot find" in str(e).lower():
            return True, "already gone"
        return False, f"{e.__class__.__name__}: {e}"
    except Exception as e:
        return False, f"{e.__class__.__name__}: {e}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true",
                    help="Actually delete files + rows. Default: dry-run.")
    ap.add_argument("--keep-newest", action="store_true",
                    help="Keep the newest-committed row instead of the oldest.")
    args = ap.parse_args()

    conn = connect()
    order = "DESC" if args.keep_newest else "ASC"

    clusters = conn.execute("""
        SELECT text_hash, COUNT(*) AS n
        FROM pdfs
        WHERE text_hash IS NOT NULL AND committed_at IS NOT NULL
        GROUP BY text_hash
        HAVING COUNT(*) > 1
        ORDER BY n DESC
    """).fetchall()

    if not clusters:
        print("[ok] no content-duplicate clusters — nothing to prune.")
        return

    total_extras = 0
    ok_files = fail_files = row_removed = 0
    mode = "DELETE" if args.confirm else "DRY-RUN"
    print(f"[{mode}] {len(clusters)} cluster(s) with content duplicates. "
          f"Keeping the {'newest' if args.keep_newest else 'oldest'}-committed row per cluster.\n")

    for cluster in clusters:
        h = cluster["text_hash"]
        rows = conn.execute(
            f"""
            SELECT id, host, dest_path, committed_at, proposed_name
            FROM pdfs
            WHERE text_hash = %s AND committed_at IS NOT NULL
            ORDER BY committed_at {order}, id {order}
            """,
            (h,),
        ).fetchall()
        keeper = rows[0]
        extras = rows[1:]
        total_extras += len(extras)

        print(f"cluster text_hash={h[:16]}…  ({len(rows)} rows, keeping id={keeper['id']})")
        print(f"  keep:   id={keeper['id']:>4}  {keeper['dest_path']}")
        for r in extras:
            print(f"  drop:   id={r['id']:>4}  {r['dest_path']}")
            if args.confirm:
                if r["dest_path"]:
                    ok, note = _delete_smb(r["dest_path"])
                    if ok:
                        ok_files += 1
                        print(f"    [file] {note}")
                    else:
                        fail_files += 1
                        print(f"    [file-fail] {note}")
                        continue  # don't drop the DB row if the file didn't go
                conn.execute("DELETE FROM pdfs WHERE id = %s", (r["id"],))
                row_removed += 1
        print()

    if args.confirm:
        conn.commit()
        print(f"[done] files_deleted={ok_files}  file_fail={fail_files}  rows_removed={row_removed}")
    else:
        print(f"[dry-run] would delete {total_extras} file(s) + row(s). Re-run with --confirm.")


if __name__ == "__main__":
    main()
