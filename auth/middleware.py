"""Resolve AuthContext from an incoming request.

Called by the require() FastAPI dependency (see permissions.py). Kept as
a separate module because the DB-touching path is meatier than the pure
permission-check logic.
"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncConnection

from .context import ANONYMOUS, AuthContext
from .permissions import resolve_for_role
from .service import SessionRevoked
from .sessions import SessionService
from .token_state import is_jti_revoked
from .tokens import TokenError, TokenService


def _bearer(request: Request) -> str | None:
    hdr = request.headers.get("authorization") or request.headers.get("Authorization")
    if not hdr:
        return None
    if not hdr.lower().startswith("bearer "):
        return None
    return hdr[7:].strip() or None


async def resolve(
    request: Request,
    *,
    conn: AsyncConnection,
    tokens: TokenService,
    sessions: SessionService,
) -> AuthContext:
    """Read Bearer, verify, return AuthContext. Never raises for anonymous —
    caller (require()) decides whether anonymous is acceptable.
    """
    token = _bearer(request)
    if not token:
        return _with_request_id(ANONYMOUS, request)

    try:
        claims = tokens.verify_access(token)
    except TokenError:
        # Signature / iss / aud / exp problems → anonymous. require() → 401.
        return _with_request_id(ANONYMOUS, request)

    # jti revoked?
    if await is_jti_revoked(conn, claims.jti):
        raise SessionRevoked("access token revoked")

    # Session + user still valid?
    state = await sessions.session_and_user_state(conn, claims.sid)
    if state is None or state["revoked_at"] is not None:
        raise SessionRevoked("session no longer active")
    if state["user_ver"] != claims.ver:
        raise SessionRevoked("token version out of date")
    if state["status"] != "ACTIVE":
        raise SessionRevoked("user not active")

    ctx = AuthContext(
        actor_kind="user",
        user_id=claims.sub,
        session_id=claims.sid,
        role=claims.role,
        permissions=resolve_for_role(claims.role),
        request_id=getattr(request.state, "request_id", "") or request.headers.get("x-request-id", ""),
    )
    request.state.auth = ctx
    return ctx


def _with_request_id(ctx: AuthContext, request: Request) -> AuthContext:
    rid = getattr(request.state, "request_id", "") or request.headers.get("x-request-id", "")
    if rid == ctx.request_id:
        return ctx
    # dataclass is frozen — replace via new instance
    from dataclasses import replace
    return replace(ctx, request_id=rid)
