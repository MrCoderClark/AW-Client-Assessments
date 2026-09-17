"""Runtime app-settings endpoints — /api/v1/settings.

Admin-only. GET returns the current settings; PATCH flips one or more.
Backed by auth/app_settings.py (the app_settings table).

  GET   /api/v1/settings   require system:read
  PATCH /api/v1/settings   require system:write
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncConnection

from . import app_settings
from .audit import AuditEvent, audit_logger
from .context import AuthContext
from .deps import DbConn
from .permissions import require

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or request.headers.get("x-request-id", "")


class SettingsPatch(BaseModel):
    sso_login_enabled: bool | None = None
    sso_allowlist_enabled: bool | None = None
    mfa_required: bool | None = None


class AllowlistAdd(BaseModel):
    # Accepts one or many. `emails` is the bulk field; `email` is a convenience
    # single. The frontend splits paste/upload input into `emails`.
    emails: list[str] | None = Field(default=None)
    email: str | None = Field(default=None, max_length=254)
    note: str | None = Field(default=None, max_length=200)


@router.get("")
async def get_settings(
    conn: Annotated[AsyncConnection, DbConn],
    ctx: Annotated[AuthContext, require("system:read")],
):
    return await app_settings.get_all(conn)


@router.patch("")
async def patch_settings(
    body: SettingsPatch,
    request: Request,
    conn: Annotated[AsyncConnection, DbConn],
    ctx: Annotated[AuthContext, require("system:write")],
):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        return await app_settings.get_all(conn)
    try:
        result = await app_settings.set_many(conn, updates, actor_id=ctx.user_id)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"unknown setting: {e}")

    await audit_logger().emit(
        None, ctx,   # own committed tx — never the request conn (advisory-lock deadlock)
        AuditEvent(action="SETTINGS_UPDATED", severity="SEC",
                   target_type="app_settings",
                   context={"changed": updates}),
        actor_ip=(request.client.host if request.client else None),
        user_agent=request.headers.get("user-agent", "")[:512],
    )
    return result


# ---------- SSO allowlist -------------------------------------------

@router.get("/sso-allowlist")
async def get_allowlist(
    conn: Annotated[AsyncConnection, DbConn],
    ctx: Annotated[AuthContext, require("system:read")],
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    result = await app_settings.sso_allowlist_page(conn, q=q, limit=limit, offset=offset)
    return {**result, "limit": min(max(1, limit), 200), "offset": max(0, offset)}


@router.post("/sso-allowlist", status_code=201)
async def add_allowlist(
    body: AllowlistAdd,
    request: Request,
    conn: Annotated[AsyncConnection, DbConn],
    ctx: Annotated[AuthContext, require("system:write")],
):
    emails = list(body.emails or [])
    if body.email:
        emails.append(body.email)
    if not emails:
        raise HTTPException(status_code=400, detail="no emails provided")
    result = await app_settings.sso_allowlist_add_many(conn, emails, body.note, actor_id=ctx.user_id)
    if result["added"]:
        await audit_logger().emit(
            None, ctx,
            AuditEvent(action="SSO_ALLOWLIST_ADDED", severity="SEC",
                       target_type="sso_allowlist",
                       context={"added": result["added"], "skipped": result["skipped"]}),
            actor_ip=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent", "")[:512],
        )
    return result


@router.delete("/sso-allowlist/{email}")
async def remove_allowlist(
    email: str,
    request: Request,
    conn: Annotated[AsyncConnection, DbConn],
    ctx: Annotated[AuthContext, require("system:write")],
):
    await app_settings.sso_allowlist_remove(conn, email)
    await audit_logger().emit(
        None, ctx,
        AuditEvent(action="SSO_ALLOWLIST_REMOVED", severity="SEC",
                   target_type="sso_allowlist", context={"email": email.strip().lower()}),
        actor_ip=(request.client.host if request.client else None),
        user_agent=request.headers.get("user-agent", "")[:512],
    )
    return {"ok": True}
