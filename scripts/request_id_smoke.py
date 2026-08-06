"""Request-ID + structured-log smoke.

Verifies:
  1. Response echoes back a client-provided X-Request-ID unchanged.
  2. When the client sends no header, one is auto-generated (32 hex).
  3. Audit rows persisted during a request carry that same request_id.
  4. The access logger emits a JSON line containing the request_id.

Run:  uv run --env-file .env python scripts/request_id_smoke.py
"""
from __future__ import annotations

import io
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from api import app
from auth.observability import configure_logging
from db import connect as sync_connect


def _grab_access_logs() -> tuple[io.StringIO, logging.Handler]:
    """Attach a memory handler to the access logger so we can inspect
    the JSON lines emitted by RequestIdMiddleware."""
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    # match the JSON formatter installed at boot
    from auth.observability import _JsonFormatter  # noqa: PLC2701 (test-only)
    h.setFormatter(_JsonFormatter())
    logging.getLogger("cfv.access").addHandler(h)
    return buf, h


def _audit_row_for(request_id: str) -> dict | None:
    conn = sync_connect()
    r = conn.execute(
        "SELECT id, action, request_id FROM audit_events "
        "WHERE request_id = %s ORDER BY at DESC LIMIT 1",
        (request_id,),
    ).fetchone()
    conn.close()
    return dict(r) if r else None


def main() -> None:
    configure_logging("INFO")
    buf, h = _grab_access_logs()

    with TestClient(app) as c:
        # ---- 1. echo client-supplied header ------------------------------
        supplied = "test-req-12345"
        r = c.get("/api/health", headers={"X-Request-ID": supplied})
        assert r.status_code == 200
        assert r.headers.get("X-Request-ID") == supplied, \
            f"expected echoed {supplied}, got {r.headers.get('X-Request-ID')}"
        print(f"[ok] client-supplied X-Request-ID echoed: {supplied}")

        # ---- 2. auto-generated when absent -------------------------------
        r = c.get("/api/health")
        gen = r.headers.get("X-Request-ID")
        assert gen and re.fullmatch(r"[0-9a-f]{32}", gen), \
            f"auto-generated id should be 32-hex, got {gen!r}"
        print(f"[ok] auto-generated X-Request-ID: {gen}")

        # ---- 3. audit row carries the request_id -------------------------
        forced = "smoke-rid-abcdef"
        r = c.post(
            "/api/v1/auth/login",
            headers={"X-Request-ID": forced},
            json={"email": "ghost@nowhere.example", "password": "x"},
        )
        assert r.status_code == 401
        assert r.headers.get("X-Request-ID") == forced
        row = _audit_row_for(forced)
        assert row is not None, f"no audit row for request_id={forced}"
        assert row["action"] == "AUTH_LOGIN_FAILURE", row
        print(f"[ok] audit row carries request_id ({row['action']})")

    # ---- 4. access log contains a JSON line with request_id --------------
    logging.getLogger("cfv.access").removeHandler(h)
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert lines, "no access log lines captured"
    hit = None
    for ln in lines:
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if d.get("request_id") == forced:
            hit = d
            break
    assert hit is not None, f"no access log line for {forced}; got {lines}"
    for k in ("method", "path", "status", "dur_ms", "request_id"):
        assert k in hit, f"missing key {k} in {hit}"
    assert hit["path"] == "/api/v1/auth/login"
    assert hit["status"] == 401
    print(f"[ok] access log JSON line: {hit}")
    print("[done]")


if __name__ == "__main__":
    main()
