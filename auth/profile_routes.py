"""Dashboard profile CRUD — /api/v1/profiles/*.

Profiles are named bundles that map a user to a code-defined dashboard
layout (`layout_key`). System profiles (`is_system = true`) are seeded by
migration `c3d4e5f6a1b2` and cannot be renamed or deleted.

Layouts themselves live in the frontend as React components; the backend
only stores the key. Adding a new layout is a two-step change (frontend
component + this LAYOUT_KEYS list).

ponytail: raw SQL inline, no service layer. Surface is 4 endpoints.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .audit import AuditEvent, audit_logger
from .context import AuthContext
from .deps import DbConn
from .permissions import require
from .random import new_id

router = APIRouter(prefix="/api/v1/profiles", tags=["profiles"])

# Keep in sync with frontend/app/_components/dashboards/*.
LAYOUT_KEYS: frozenset[str] = frozenset({"ops_default", "fleet_health", "records"})


# ---------- request/response models --------------------------------

class ProfileRow(BaseModel):
    id: str
    name: str
    description: str | None
    layout_key: str
    is_system: bool
    user_count: int
    created_at: str
    updated_at: str


class ProfileList(BaseModel):
    profiles: list[ProfileRow]


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    layout_key: str


class ProfilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    layout_key: str | None = None


# ---------- helpers -------------------------------------------------

def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


def _client_ua(request: Request) -> str:
    return request.headers.get("user-agent", "")[:512]


def _row_to_model(row: dict) -> ProfileRow:
    def _iso(v):
        return v.isoformat() if v is not None else ""
    return ProfileRow(
        id=str(row["id"]),
        name=str(row["name"]),
        description=row["description"],
        layout_key=str(row["layout_key"]),
        is_system=bool(row["is_system"]),
        user_count=int(row.get("user_count") or 0),
        created_at=_iso(row["created_at"]),
        updated_at=_iso(row["updated_at"]),
    )


def _validate_layout(key: str) -> None:
    if key not in LAYOUT_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown layout_key '{key}' (valid: {sorted(LAYOUT_KEYS)})",
        )


async def _load(conn: AsyncConnection, profile_id: str) -> dict:
    r = await conn.execute(
        text("""
            SELECT p.id, p.name, p.description, p.layout_key, p.is_system,
                   p.created_at, p.updated_at,
                   (SELECT COUNT(*) FROM users WHERE profile_id = p.id) AS user_count
            FROM profiles p
            WHERE p.id = :id
        """),
        {"id": profile_id},
    )
    row = r.first()
    if row is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return dict(row._mapping)


# ---------- endpoints -----------------------------------------------

@router.get("", response_model=ProfileList)
async def list_profiles(
    conn: Annotated[AsyncConnection, DbConn],
    ctx: Annotated[AuthContext, require("user:read")],
):
    r = await conn.execute(
        text("""
            SELECT p.id, p.name, p.description, p.layout_key, p.is_system,
                   p.created_at, p.updated_at,
                   (SELECT COUNT(*) FROM users WHERE profile_id = p.id) AS user_count
            FROM profiles p
            ORDER BY p.is_system DESC, p.name ASC
        """)
    )
    rows = [dict(row._mapping) for row in r.all()]
    return ProfileList(profiles=[_row_to_model(row) for row in rows])


@router.post("", response_model=ProfileRow, status_code=201)
async def create_profile(
    body: ProfileCreate,
    request: Request,
    conn: Annotated[AsyncConnection, DbConn],
    ctx: Annotated[AuthContext, require("user:write")],
):
    _validate_layout(body.layout_key)
    name = body.name.strip()
    # Case-insensitive uniqueness check for a nicer error than an FK/unique
    # violation would give.
    dup = await conn.execute(
        text("SELECT id FROM profiles WHERE lower(name) = lower(:n)"),
        {"n": name},
    )
    if dup.first() is not None:
        raise HTTPException(status_code=409, detail="profile name already in use")

    pid = new_id()
    await conn.execute(
        text("""
            INSERT INTO profiles (id, name, description, layout_key, is_system)
            VALUES (:id, :name, :desc, :lk, false)
        """),
        {"id": pid, "name": name, "desc": body.description, "lk": body.layout_key},
    )
    row = await _load(conn, pid)
    await audit_logger().emit(
        None, ctx,
        AuditEvent(action="PROFILE_CREATED", target_type="profile", target_id=pid,
                   context={"name": name, "layout_key": body.layout_key}),
        actor_ip=_client_ip(request), user_agent=_client_ua(request),
    )
    return _row_to_model(row)


@router.patch("/{profile_id}", response_model=ProfileRow)
async def update_profile(
    profile_id: str,
    body: ProfilePatch,
    request: Request,
    conn: Annotated[AsyncConnection, DbConn],
    ctx: Annotated[AuthContext, require("user:write")],
):
    current = await _load(conn, profile_id)

    updates: list[str] = []
    params: dict = {"id": profile_id}
    changed: dict = {}

    if body.name is not None:
        new_name = body.name.strip()
        if current["is_system"] and new_name.lower() != str(current["name"]).lower():
            raise HTTPException(status_code=409, detail="system profiles cannot be renamed")
        if new_name.lower() != str(current["name"]).lower():
            dup = await conn.execute(
                text("SELECT id FROM profiles WHERE lower(name) = lower(:n) AND id <> :id"),
                {"n": new_name, "id": profile_id},
            )
            if dup.first() is not None:
                raise HTTPException(status_code=409, detail="profile name already in use")
            updates.append("name = :name")
            params["name"] = new_name
            changed["name"] = new_name

    if body.description is not None:
        updates.append("description = :desc")
        params["desc"] = body.description
        changed["description"] = body.description

    if body.layout_key is not None and body.layout_key != current["layout_key"]:
        _validate_layout(body.layout_key)
        updates.append("layout_key = :lk")
        params["lk"] = body.layout_key
        changed["layout_key"] = body.layout_key

    if not updates:
        return _row_to_model(current)

    updates.append("updated_at = now()")
    await conn.execute(
        text(f"UPDATE profiles SET {', '.join(updates)} WHERE id = :id"),
        params,
    )
    row = await _load(conn, profile_id)
    await audit_logger().emit(
        None, ctx,
        AuditEvent(action="PROFILE_UPDATED", target_type="profile", target_id=profile_id,
                   context={"changed": changed}),
        actor_ip=_client_ip(request), user_agent=_client_ua(request),
    )
    return _row_to_model(row)


@router.delete("/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: str,
    request: Request,
    conn: Annotated[AsyncConnection, DbConn],
    ctx: Annotated[AuthContext, require("user:write")],
):
    current = await _load(conn, profile_id)
    if current["is_system"]:
        raise HTTPException(status_code=409, detail="system profiles cannot be deleted")
    if int(current["user_count"]) > 0:
        raise HTTPException(
            status_code=409,
            detail=f"profile is assigned to {current['user_count']} user(s); reassign them first",
        )
    await conn.execute(text("DELETE FROM profiles WHERE id = :id"), {"id": profile_id})
    await audit_logger().emit(
        None, ctx,
        AuditEvent(action="PROFILE_DELETED", target_type="profile", target_id=profile_id,
                   context={"name": str(current["name"])}),
        actor_ip=_client_ip(request), user_agent=_client_ua(request),
    )
    from fastapi import Response
    return Response(status_code=204)
