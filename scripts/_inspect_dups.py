"""Ad-hoc: show all rows that share a client's proposed_name."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import connect

pattern = sys.argv[1] if len(sys.argv) > 1 else "VIA_Character_Strengths_Profile-Juancarlos_Valdezperez%"
c = connect()
rows = c.execute(
    """
    SELECT id, host, filename, proposed_name, md5, size, committed_at, dest_path, indexed_at
    FROM pdfs
    WHERE proposed_name LIKE %s
    ORDER BY committed_at NULLS LAST, indexed_at
    """,
    (pattern,),
).fetchall()
if not rows:
    print("no rows match")
    sys.exit(0)
for r in rows:
    md5 = r["md5"] or "NULL"
    print(f"id={r['id']:>4}  host={r['host']:<16}  md5={md5:<34}  size={r['size']:>8}  committed={str(r['committed_at'])[:19]}")
    print(f"      src:  {r['filename']}")
    print(f"      dest: {r['dest_path']}")
    print()
print(f"total: {len(rows)}   distinct md5s: {len(set((r['md5'] or 'NULL') for r in rows))}")
print(f"distinct sizes: {sorted(set(r['size'] for r in rows))}")
