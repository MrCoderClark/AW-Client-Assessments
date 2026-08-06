"""/api/v1/notifications router — list, mark-read, SSE stream."""
from __future__ import annotations

import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncConnection

from .context import AuthContext
from .deps import DbConn
from .notifications import (
    bus, list_for_user, mark_all_read, mark_read,
)
from .permissions import current_user_dep

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    conn: Annotated[AsyncConnection, DbConn],
    ctx: Annotated[AuthContext, current_user_dep()],
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    since_id: str | None = None,
    category: str | None = Query(
        default=None,
        pattern="^(scan_commit|security|user_lifecycle|pc_health)$",
    ),
    severity: str | None = Query(default=None, pattern="^(INFO|WARN|SEC)$"),
    unread_only: bool = False,
):
    rows, total, unread = await list_for_user(
        conn, user_id=ctx.user_id or "",
        limit=limit, offset=offset, since_id=since_id,
        category=category, severity=severity, unread_only=unread_only,
    )
    return {"notifications": rows, "total": total, "unread": unread,
            "limit": limit, "offset": offset}


@router.post("/{notif_id}/read", status_code=204)
async def mark_one_read(
    notif_id: str,
    conn: Annotated[AsyncConnection, DbConn],
    ctx: Annotated[AuthContext, current_user_dep()],
):
    ok = await mark_read(conn, user_id=ctx.user_id or "", notif_id=notif_id)
    if not ok:
        raise HTTPException(status_code=404, detail="notification not found or already read")
    return


@router.post("/read-all")
async def mark_all(
    conn: Annotated[AsyncConnection, DbConn],
    ctx: Annotated[AuthContext, current_user_dep()],
):
    n = await mark_all_read(conn, user_id=ctx.user_id or "")
    return {"marked": n}


@router.get("/stream")
async def stream(
    request: Request,
    ctx: Annotated[AuthContext, current_user_dep()],
):
    """SSE stream of new notifications for the current user.

    Emits `event: notification` frames with JSON payload. Sends a `: ping`
    comment every 25s so idle proxies don't close the connection. The
    frontend also polls `/notifications?since_id=` on reconnect to catch
    any events missed while offline.
    """
    user_id = ctx.user_id or ""
    q = await bus().subscribe(user_id)

    async def gen():
        try:
            # Initial hello so the client knows the pipe is live
            yield b": connected\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=25.0)
                except asyncio.TimeoutError:
                    yield b": ping\n\n"
                    continue
                if await request.is_disconnected():
                    break
                data = json.dumps(payload, ensure_ascii=False)
                yield f"event: notification\ndata: {data}\n\n".encode("utf-8")
        finally:
            await bus().unsubscribe(user_id, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # nginx pass-through
            "Connection": "keep-alive",
        },
    )
