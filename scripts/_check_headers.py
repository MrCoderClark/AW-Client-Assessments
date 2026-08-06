import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from api import app

with TestClient(app) as c:
    r = c.get("/api/health")
    for k in ("Content-Security-Policy", "X-Content-Type-Options", "X-Frame-Options",
              "Referrer-Policy", "Permissions-Policy"):
        v = r.headers.get(k, "")
        print(f"  {k}: {v[:80]}{'…' if len(v) > 80 else ''}")
    print(f"  Set-Cookie: {r.headers.get('set-cookie', '(none)')[:120]}")
    # HSTS should be absent over HTTP:
    print(f"  Strict-Transport-Security: {r.headers.get('strict-transport-security', '(absent, http mode)')}")

    # Simulate HTTPS via X-Forwarded-Proto:
    print()
    print("With X-Forwarded-Proto: https →")
    r = c.get("/api/health", headers={"X-Forwarded-Proto": "https"})
    print(f"  Strict-Transport-Security: {r.headers.get('strict-transport-security', '(absent!)')}")
