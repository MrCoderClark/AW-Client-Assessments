import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db import connect

c = connect()
rows = c.execute("""
    SELECT id, host, filename, proposed_name, dest_path, committed_at
    FROM pdfs
    WHERE filename LIKE %s
    ORDER BY id
""", ("%(1)%",)).fetchall()
for r in rows:
    print(f"id={r['id']}  host={r['host']}")
    print(f"  filename:      {r['filename']}")
    print(f"  proposed_name: {r['proposed_name']}")
    print(f"  dest_path:     {r['dest_path']}")
    print()
print(f"total: {len(rows)}")
