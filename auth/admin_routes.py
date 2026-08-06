"""Admin user-management endpoints — /api/v1/users/*.

M2 scope: list, get, update, suspend, reactivate, force-reset, soft-delete,
hard-delete. Role/MFA-reset endpoints deferred to M3+.

ponytail: raw SQL inline against the existing users table — no ORM,
no separate service layer. Small surface (7 endpoints, ~35 SQL lines).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .audit import AuditEvent, audit_logger
from .context import AuthContext
from .deps import DbConn, dep_auth_service
from .notifications import NotificationEvent, emit_for_category
from .emails import app_base_url, password_reset_email, send_mail
from .permissions import require
from .random import new_id, opaque_token
from .service import AuthService

router = APIRouter(prefix="/api/v1/users", tags=["admin-users"])


# ---------- helpers -------------------------------------------------

def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


def _client_ua(request: Request) -> str:
    return request.headers.get("user-agent", "")[:512]


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or request.headers.get("x-request-id", "")


async def _load_user(conn: AsyncConnection, user_id: str) -> dict:
    r = await conn.execute(
        text("""
            SELECT u.id, u.email, u.email_normalized, u.first_name, u.last_name, u.display_name,
                   u.role, u.status, u.must_change_password,
                   u.mfa_enrolled_at IS NOT NULL AS mfa_enrolled,
                   u.email_verified_at, u.ver, u.failed_login_attempts, u.locked_until,
                   u.last_login_at, u.last_login_ip, u.suspended_at, u.suspended_reason,
                   u.deleted_at, u.created_at, u.updated_at,
                   u.profile_id, p.name AS profile_name, p.layout_key AS profile_layout_key
            FROM users u
            LEFT JOIN profiles p ON p.id = u.profile_id
            WHERE u.id = :id
        """),
        {"id": user_id},
    )
    row = r.first()
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")
    return dict(row._mapping)


async def _notify_lifecycle(kind: str, title: str, body: str, user_id: str, actor: AuthContext, severity: str = "INFO") -> None:
    await emit_for_category(NotificationEvent(
        category="user_lifecycle", kind=kind, severity=severity,
        title=title, body=body, url="/admin/users",
        context={"user_id": user_id, "actor_id": actor.user_id},
    ))


def _self_guard(ctx: AuthContext, target_id: str, action: str) -> None:
    if ctx.user_id == target_id:
        raise HTTPException(
            status_code=409,
            detail=f"admins cannot {action} their own account",
        )


# ---------- request/response models --------------------------------

class UserProfileSummary(BaseModel):
    id: str
    name: str
    layout_key: str


class UserRow(BaseModel):
    id: str
    email: str
    first_name: str | None
    last_name: str | None
    display_name: str | None
    role: str
    status: str
    must_change_password: bool
    mfa_enrolled: bool
    email_verified: bool
    ver: int
    failed_login_attempts: int
    locked_until: str | None
    last_login_at: str | None
    last_login_ip: str | None
    suspended_at: str | None
    suspended_reason: str | None
    deleted_at: str | None
    created_at: str
    updated_at: str
    profile: UserProfileSummary | None


class UserList(BaseModel):
    users: list[UserRow]
    total: int
    limit: int
    offset: int


class UserPatch(BaseModel):
    first_name: str | None = Field(default=None, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)
    display_name: str | None = Field(default=None, max_length=160)
    email: str | None = Field(default=None, min_length=3, max_length=254)
    role: str | None = Field(default=None, pattern="^(admin|operator|viewer)$")
    # Empty string clears the profile assignment (matches how the client
    # sends "unassigned"); a UUID string sets it; omission leaves it alone.
    profile_id: str | None = Field(default=None)


class SuspendBody(BaseModel):
    reason: str = Field(default="", max_length=500)


class ForceResetResponse(BaseModel):
    user_id: str
    reset_url: str
    mail_ok: bool
    mail_note: str


def _row_to_model(row: dict) -> UserRow:
    def _iso(v):
        return v.isoformat() if v is not None else None
    profile = None
    if row.get("profile_id") is not None:
        profile = UserProfileSummary(
            id=str(row["profile_id"]),
            name=str(row["profile_name"]),
            layout_key=str(row["profile_layout_key"]),
        )
    return UserRow(
        id=str(row["id"]),
        email=row["email"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        display_name=row["display_name"],
        role=str(row["role"]),
        status=str(row["status"]),
        must_change_password=bool(row["must_change_password"]),
        mfa_enrolled=bool(row["mfa_enrolled"]),
        email_verified=row["email_verified_at"] is not None,
        ver=int(row["ver"]),
        failed_login_attempts=int(row["failed_login_attempts"]),
        locked_until=_iso(row["locked_until"]),
        last_login_at=_iso(row["last_login_at"]),
        last_login_ip=row["last_login_ip"],
        suspended_at=_iso(row["suspended_at"]),
        suspended_reason=row["suspended_reason"],
        deleted_at=_iso(row["deleted_at"]),
        created_at=_iso(row["created_at"]) or "",
        updated_at=_iso(row["updated_at"]) or "",
        profile=profile,
    )


# ---------- endpoints -----------------------------------------------

@router.get("", response_model=UserList)
async def list_users(
    request: Request,
    conn: Annotated[AsyncConnection, DbConn],
    ctx: Annotated[AuthContext, require("user:read")],
    q: str | None = None,
    status: str | None = Query(default=None, pattern="^(INVITED|ACTIVE|SUSPENDED|DEACTIVATED|SOFT_DELETED)$"),
    role: str | None = Query(default=None, pattern="^(admin|operator|viewer)$"),
    include_deleted: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Admin user list with a few filters. JOINs profile for the drawer."""
    where = []
    params: dict = {}
    if not include_deleted:
        where.append("u.deleted_at IS NULL")
    if status:
        where.append("u.status = CAST(:st AS user_status)")
        params["st"] = status
    if role:
        where.append("u.role = CAST(:rl AS user_role)")
        params["rl"] = role
    if q:
        where.append("(u.email_normalized LIKE :q "
                     "OR lower(coalesce(u.first_name,'')) LIKE :q "
                     "OR lower(coalesce(u.last_name,'')) LIKE :q "
                     "OR lower(coalesce(u.display_name,'')) LIKE :q)")
        params["q"] = f"%{q.strip().lower()}%"
    wh = f"WHERE {' AND '.join(where)}" if where else ""

    total_r = await conn.execute(text(f"SELECT COUNT(*) FROM users u {wh}"), params)
    total = int(total_r.scalar_one() or 0)

    params["lim"] = limit
    params["off"] = offset
    r = await conn.execute(
        text(f"""
            SELECT u.id, u.email, u.email_normalized, u.first_name, u.last_name, u.display_name,
                   u.role, u.status, u.must_change_password,
                   u.mfa_enrolled_at IS NOT NULL AS mfa_enrolled,
                   u.email_verified_at, u.ver, u.failed_login_attempts, u.locked_until,
                   u.last_login_at, u.last_login_ip, u.suspended_at, u.suspended_reason,
                   u.deleted_at, u.created_at, u.updated_at,
                   u.profile_id, p.name AS profile_name, p.layout_key AS profile_layout_key
            FROM users u
            LEFT JOIN profiles p ON p.id = u.profile_id
            {wh}
            ORDER BY u.created_at DESC
            LIMIT :lim OFFSET :off
        """),
        params,
    )
    rows = [dict(row._mapping) for row in r.all()]
    return UserList(
        users=[_row_to_model(row) for row in rows],
        total=total, limit=limit, offset=offset,
    )


@router.get("/{user_id}", response_model=UserRow)
async def get_user(
    user_id: str,
    conn: Annotated[AsyncConnection, DbConn],
    ctx: Annotated[AuthContext, require("user:read")],
):
    return _row_to_model(await _load_user(conn, user_id))


@router.patch("/{user_id}", response_model=UserRow)
async def update_user(
    user_id: str,
    body: UserPatch,
    request: Request,
    conn: Annotated[AsyncConnection, DbConn],
    ctx: Annotated[AuthContext, require("user:write")],
):
    """Update mutable profile fields + role. Email changes bump `ver` so any
    active sessions on the old identity are invalidated."""
    updates: list[str] = []
    params: dict = {"id": user_id}
    changed: dict = {}

    if body.first_name is not None:
        updates.append("first_name = :fn")
        params["fn"] = body.first_name.strip()
        changed["first_name"] = params["fn"]
    if body.last_name is not None:
        updates.append("last_name = :ln")
        params["ln"] = body.last_name.strip()
        changed["last_name"] = params["ln"]
    if body.display_name is not None:
        updates.append("display_name = :dn")
        params["dn"] = body.display_name.strip()
        changed["display_name"] = params["dn"]

    role_changed = False
    if body.role is not None:
        current = await _load_user(conn, user_id)
        if str(current["role"]) != body.role:
            updates.append("role = CAST(:role AS user_role)")
            params["role"] = body.role
            # Bump ver so live tokens with the old role can no longer act.
            updates.append("ver = ver + 1")
            changed["role"] = body.role
            role_changed = True

    if body.profile_id is not None:
        pid = body.profile_id.strip() or None
        if pid is not None:
            exists = await conn.execute(
                text("SELECT id FROM profiles WHERE id = :id"), {"id": pid},
            )
            if exists.first() is None:
                raise HTTPException(status_code=400, detail="unknown profile_id")
        updates.append("profile_id = :pid")
        params["pid"] = pid
        changed["profile_id"] = pid

    if body.email is not None:
        new_email = body.email.strip()
        new_norm = new_email.lower()
        # Reject dup on a different active user.
        dup = await conn.execute(
            text("""SELECT id FROM users
                    WHERE email_normalized = :ne AND id <> :id AND deleted_at IS NULL"""),
            {"ne": new_norm, "id": user_id},
        )
        if dup.first() is not None:
            raise HTTPException(status_code=409, detail="email already in use")
        updates.append("email = :em")
        updates.append("email_normalized = :ne")
        # New email must be re-verified.
        updates.append("email_verified_at = NULL")
        updates.append("ver = ver + 1")
        params["em"] = new_email
        params["ne"] = new_norm
        changed["email"] = new_email

    if not updates:
        return _row_to_model(await _load_user(conn, user_id))

    updates.append("updated_at = now()")
    await conn.execute(
        text(f"UPDATE users SET {', '.join(updates)} WHERE id = :id"),
        params,
    )
    # If we bumped ver (role or email change), also revoke live sessions —
    # ver alone kills bearer tokens, but the refresh cookie should stop working too.
    if role_changed or "email" in changed:
        await conn.execute(
            text("""UPDATE sessions SET revoked_at = now(), revoked_reason = 'admin_update'
                    WHERE user_id = :id AND revoked_at IS NULL"""),
            {"id": user_id},
        )

    await audit_logger().emit(
        None, ctx,
        AuditEvent(action="USER_UPDATED", target_type="user", target_id=user_id,
                   context={"changed": changed}),
        actor_ip=_client_ip(request), user_agent=_client_ua(request),
    )
    return _row_to_model(await _load_user(conn, user_id))


@router.post("/{user_id}/suspend", response_model=UserRow)
async def suspend_user(
    user_id: str,
    body: SuspendBody,
    request: Request,
    conn: Annotated[AsyncConnection, DbConn],
    ctx: Annotated[AuthContext, require("user:suspend")],
):
    _self_guard(ctx, user_id, "suspend")
    await _load_user(conn, user_id)
    await conn.execute(
        text("""
            UPDATE users
            SET status = 'SUSPENDED', suspended_at = now(),
                suspended_reason = :r, ver = ver + 1, updated_at = now()
            WHERE id = :id
        """),
        {"r": body.reason or None, "id": user_id},
    )
    await conn.execute(
        text("""UPDATE sessions SET revoked_at = now(), revoked_reason = 'suspended'
                WHERE user_id = :id AND revoked_at IS NULL"""),
        {"id": user_id},
    )
    await audit_logger().emit(
        None, ctx,
        AuditEvent(action="USER_SUSPENDED", outcome="denied", severity="SEC",
                   target_type="user", target_id=user_id,
                   context={"reason": body.reason}),
        actor_ip=_client_ip(request), user_agent=_client_ua(request),
    )
    row = await _load_user(conn, user_id)
    await _notify_lifecycle(
        "user_suspended", "User suspended",
        f"{row['email']} was suspended" + (f" — {body.reason}" if body.reason else ""),
        user_id, ctx, severity="WARN",
    )
    return _row_to_model(row)


@router.post("/{user_id}/reactivate", response_model=UserRow)
async def reactivate_user(
    user_id: str,
    request: Request,
    conn: Annotated[AsyncConnection, DbConn],
    ctx: Annotated[AuthContext, require("user:suspend")],
):
    row = await _load_user(conn, user_id)
    if row["status"] == "SOFT_DELETED":
        # ponytail: reactivating a deleted user is a distinct workflow (spec
        # FR-USR-08) — restore first, then reactivate. Not built until asked.
        raise HTTPException(status_code=409,
                            detail="cannot reactivate a deleted user; restore first")
    await conn.execute(
        text("""
            UPDATE users
            SET status = 'ACTIVE', suspended_at = NULL, suspended_reason = NULL,
                updated_at = now()
            WHERE id = :id
        """),
        {"id": user_id},
    )
    await audit_logger().emit(
        None, ctx,
        AuditEvent(action="USER_REACTIVATED", target_type="user", target_id=user_id),
        actor_ip=_client_ip(request), user_agent=_client_ua(request),
    )
    row = await _load_user(conn, user_id)
    await _notify_lifecycle(
        "user_reactivated", "User reactivated",
        f"{row['email']} was reactivated", user_id, ctx,
    )
    return _row_to_model(row)


@router.post("/{user_id}/force-reset", response_model=ForceResetResponse)
async def force_reset(
    user_id: str,
    request: Request,
    conn: Annotated[AsyncConnection, DbConn],
    ctx: Annotated[AuthContext, require("user:force_reset")],
    svc: Annotated[AuthService, Depends(dep_auth_service)],
):
    """Admin-triggered password reset: create a reset token, mail it,
    revoke sessions, and set must_change_password on the account."""
    row = await _load_user(conn, user_id)
    if row["deleted_at"] is not None:
        raise HTTPException(status_code=409, detail="user is deleted")

    token = opaque_token(32)
    await conn.execute(
        text("""
            INSERT INTO password_resets (id, user_id, token_hash, expires_at)
            VALUES (:id, :uid, :h, now() + interval '30 minutes')
        """),
        {"id": new_id(), "uid": user_id, "h": svc._hash_token(token)},
    )
    await conn.execute(
        text("""UPDATE users SET must_change_password = true, ver = ver + 1,
                                 updated_at = now()
                WHERE id = :id"""),
        {"id": user_id},
    )
    await conn.execute(
        text("""UPDATE sessions SET revoked_at = now(), revoked_reason = 'force_reset'
                WHERE user_id = :id AND revoked_at IS NULL"""),
        {"id": user_id},
    )

    url = f"{app_base_url().rstrip('/')}/reset-password?token={token}"
    display = row["display_name"] or f"{row['first_name'] or ''} {row['last_name'] or ''}".strip() or row["email"]
    ok, note = send_mail(row["email"], password_reset_email(display, url))

    await audit_logger().emit(
        None, ctx,
        AuditEvent(action="USER_FORCE_RESET", severity="SEC",
                   target_type="user", target_id=user_id,
                   context={"mail_ok": ok}),
        actor_ip=_client_ip(request), user_agent=_client_ua(request),
    )
    await _notify_lifecycle(
        "user_force_reset", "Password force-reset issued",
        f"Reset link {'mailed to' if ok else 'generated for'} {row['email']}",
        user_id, ctx, severity="WARN",
    )
    return ForceResetResponse(user_id=user_id, reset_url=url, mail_ok=ok, mail_note=note)


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    request: Request,
    conn: Annotated[AsyncConnection, DbConn],
    ctx: Annotated[AuthContext, require("user:delete")],
    hard: bool = False,
):
    """Soft-delete by default (status=SOFT_DELETED, deleted_at set, sessions
    revoked, ver bumped). `?hard=true` fully removes the row."""
    _self_guard(ctx, user_id, "delete")
    target = await _load_user(conn, user_id)

    if hard:
        await conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        await audit_logger().emit(
            None, ctx,
            AuditEvent(action="USER_HARD_DELETED", severity="SEC",
                       target_type="user", target_id=user_id),
            actor_ip=_client_ip(request), user_agent=_client_ua(request),
        )
        await _notify_lifecycle(
            "user_hard_deleted", "User permanently deleted",
            f"{target['email']} was hard-deleted", user_id, ctx, severity="WARN",
        )
        from fastapi import Response
        return Response(status_code=204)

    await conn.execute(
        text("""
            UPDATE users
            SET status = 'SOFT_DELETED', deleted_at = now(), deleted_by = :by,
                ver = ver + 1, updated_at = now()
            WHERE id = :id
        """),
        {"by": ctx.user_id, "id": user_id},
    )
    await conn.execute(
        text("""UPDATE sessions SET revoked_at = now(), revoked_reason = 'soft_delete'
                WHERE user_id = :id AND revoked_at IS NULL"""),
        {"id": user_id},
    )
    await audit_logger().emit(
        None, ctx,
        AuditEvent(action="USER_SOFT_DELETED", severity="SEC",
                   target_type="user", target_id=user_id),
        actor_ip=_client_ip(request), user_agent=_client_ua(request),
    )
    await _notify_lifecycle(
        "user_soft_deleted", "User soft-deleted",
        f"{target['email']} was soft-deleted", user_id, ctx, severity="WARN",
    )
    from fastapi import Response
    return Response(status_code=204)
