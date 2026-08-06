"""RBAC permission resolver + FastAPI `require()` dependency.

M1–M5 use the fixed role → permissions map below. The `roles`/`permissions`
tables are deferred to M6 (custom roles), so this is table-free.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncConnection
from starlette import status

from .context import ANONYMOUS, AuthContext

# ---------- Fixed role → permissions map (M1–M5) ------------------------
# See docs/PHASE15_AUTH.md §A.2 and §B.0.2.

_ADMIN = frozenset({
    # data
    "pdf:read", "pdf:write", "pdf:delete", "pdf:archive",
    "pc:read", "pc:write", "pc:browse", "pc:upload",
    "log:read",
    "run:read", "run:trigger",
    "schedule:read", "schedule:write",
    # admin
    "user:read", "user:write", "user:invite", "user:suspend", "user:delete", "user:force_reset",
    "role:read", "role:assign",
    "audit:read",
    "security:read", "security:write",
    "system:read", "system:write",
    "api_key:read", "api_key:write",
})

_OPERATOR = frozenset({
    "pdf:read", "pdf:write", "pdf:archive",
    "pc:read", "pc:browse", "pc:upload", "pc:write",
    "log:read",
    "run:read", "run:trigger",
    "schedule:read",
})

_VIEWER = frozenset({
    "pdf:read",
    "pc:read",
    "log:read",
    "run:read",
    "schedule:read",
})

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": _ADMIN,
    "operator": _OPERATOR,
    "viewer": _VIEWER,
}


def resolve_for_role(role: str) -> frozenset[str]:
    """Return the permission set for a system role. Unknown role → empty."""
    return ROLE_PERMISSIONS.get(role, frozenset())


# ---------- FastAPI plumbing --------------------------------------------

async def _resolve(request: Request, conn: AsyncConnection) -> AuthContext:
    """Deferred import to break a cycle (middleware → deps → passwords, etc.)."""
    from .deps import sessions_svc, tokens
    from .middleware import resolve
    return await resolve(request, conn=conn, tokens=tokens(), sessions=sessions_svc())


def get_context(request: Request) -> AuthContext:
    """FastAPI dependency for the raw context (no DB check, no perm check)."""
    ctx: AuthContext | None = getattr(request.state, "auth", None)
    return ctx if ctx is not None else ANONYMOUS


def require(*needed: str):
    """FastAPI dependency: verify the caller's access token and enforce
    the listed permissions. Anonymous → 401; missing perm → 403.
    """
    if not needed:
        raise ValueError("require() called with no permissions")
    needed_set = frozenset(needed)

    async def dep(request: Request) -> AuthContext:
        from .audit import AuditEvent, audit_logger
        from .engine import transaction
        async with transaction() as conn:
            ctx = await _resolve(request, conn)
        if not ctx.is_authenticated:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
            )
        missing = needed_set - ctx.permissions
        if missing:
            missing_perm = sorted(missing)[0]
            # ponytail: audit denials in a fresh committed tx so the
            # subsequent raise doesn't roll it back.
            async with transaction() as audit_conn:
                await audit_logger().emit(
                    audit_conn, ctx,  # keep atomic with the in-flight tx (which we then commit)
                    AuditEvent(action="PERMISSION_DENIED", outcome="denied",
                               target_type="endpoint", target_id=request.url.path,
                               context={"required": missing_perm,
                                        "method": request.method,
                                        "role": ctx.role}),
                    actor_ip=(request.client.host if request.client else None),
                    user_agent=request.headers.get("user-agent", "")[:512],
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"missing permission: {missing_perm}",
            )
        return ctx

    return Depends(dep)


def current_user_dep():
    """FastAPI dependency for endpoints that need auth but no specific
    permission (e.g., /auth/me, /auth/logout). Just verifies the token.
    """
    async def dep(request: Request) -> AuthContext:
        from .engine import transaction
        async with transaction() as conn:
            ctx = await _resolve(request, conn)
        if not ctx.is_authenticated:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
            )
        return ctx
    return Depends(dep)
