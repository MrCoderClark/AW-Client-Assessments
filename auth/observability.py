"""Request-ID middleware + structured JSON logging.

ponytail: one middleware + one formatter, no OTel, no third-party log
libs. Every request gets an `X-Request-ID` (from the client or fresh),
stashed in `request.state.request_id` and a ContextVar so any code
reachable from the request handler can pull it. Same id is echoed on
the response and used by audit rows.
"""
from __future__ import annotations

import logging
import sys
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

HEADER = "X-Request-ID"
_HDR_LOWER = HEADER.lower()

request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def current_request_id() -> str:
    """Return the request-id for the running request, or '' when not in one."""
    return request_id_var.get()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Ensure every request has an `X-Request-ID`.

    - Read from the incoming header when present (client-provided), else
      mint a uuid4.
    - Stash on `request.state.request_id` and a ContextVar.
    - Echo the same value back on the response.
    - Emit one structured log line per request with the timing + status.
    """

    _log = logging.getLogger("cfv.access")

    async def dispatch(self, request: Request, call_next):
        rid = (request.headers.get(_HDR_LOWER) or "").strip() or uuid.uuid4().hex
        # cap length so a malicious client can't blow up logs
        rid = rid[:64]
        request.state.request_id = rid
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            response: Response = await call_next(request)
            status = response.status_code
            response.headers.setdefault(HEADER, rid)
            return response
        finally:
            dur_ms = round((time.perf_counter() - start) * 1000, 1)
            # user_id filled in only when a route ran the auth dep — keep it
            # optional so pre-auth and public paths still log cleanly.
            uid = None
            auth = getattr(request.state, "auth", None)
            if auth is not None and getattr(auth, "user_id", None):
                uid = auth.user_id
            self._log.info(
                "request",
                extra={
                    "request_id": rid,
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "dur_ms": dur_ms,
                    "user_id": uid,
                },
            )
            request_id_var.reset(token)


class _JsonFormatter(logging.Formatter):
    _STD = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        # base fields
        d: dict = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        # any 'extra' fields the caller passed
        for k, v in record.__dict__.items():
            if k in self._STD or k.startswith("_"):
                continue
            d[k] = v
        # fall back to the ContextVar when the caller didn't set one
        if "request_id" not in d:
            rid = request_id_var.get()
            if rid:
                d["request_id"] = rid
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        # deterministic-ish key ordering: put common fields first
        import json
        return json.dumps(d, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Attach JSON formatter to root. Idempotent."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in root.handlers:
        if getattr(h, "_cfv_json", False):
            return  # already configured
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(_JsonFormatter())
    h._cfv_json = True  # type: ignore[attr-defined]
    # Remove any default handlers uvicorn / basicConfig installed so we don't
    # double-emit each line.
    root.handlers = [h]
    # uvicorn's own loggers propagate to root — good, we don't need to touch them.
