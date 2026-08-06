"""/api/v1/auth router.

Endpoints in this branch:
  POST /login       body: { email, password, remember? }
  POST /logout      auth
  POST /logout-all  auth
  POST /refresh     body: { refresh_token } OR __Host-refresh cookie
  GET  /me          auth
  GET  /jwks        public — one-key JWKS for M1

Login/refresh return the refresh token in the response body AND set a
cookie. Postman-friendly (body) + browser-friendly (cookie).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .context import AuthContext
from .deps import DbConn, dep_auth_service, dep_tokens
from .rate_limit import check_and_consume
from .emails import (
    app_base_url,
    invite_email,
    password_changed_email,
    password_reset_email,
    send_mail,
)
from .permissions import current_user_dep, require
from .random import new_id, opaque_token
from .service import AuthService, PasswordInvalid, TokenPair
from .tokens import TokenService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

REFRESH_COOKIE = "cfv_refresh"
# ponytail: __Host- prefix requires HTTPS + Path=/ + no Domain. Use it in
# prod behind the reverse proxy; keep a plain name in dev so Postman +
# local frontends work.

# ---------- request/response models --------------------------------

class LoginBody(BaseModel):
    # ponytail: don't pull in `email-validator` just to check `@`. If we ever
    # let end users register, wire pydantic[email] then.
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1)
    remember: bool = False


class TokenBody(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    refresh_token: str


class ProfileSummaryBody(BaseModel):
    id: str
    name: str
    layout_key: str


class MeBody(BaseModel):
    user_id: str
    email: str
    first_name: str | None
    last_name: str | None
    display_name: str | None
    role: str
    status: str
    permissions: list[str]
    must_change_password: bool
    profile: ProfileSummaryBody | None
    dashboard_widgets: list[str] | None


class DashboardWidgetsBody(BaseModel):
    # null clears the override (revert to profile default). A list sets the
    # user's custom widget composition.
    widgets: list[str] | None


class RefreshBody(BaseModel):
    refresh_token: str | None = None


class LogoutAllBody(BaseModel):
    revoked: int


class AcceptInviteBody(BaseModel):
    token: str = Field(min_length=8)
    password: str = Field(min_length=1)
    first_name: str = Field(default="", max_length=80)
    last_name: str = Field(default="", max_length=80)


class VerifyEmailBody(BaseModel):
    token: str = Field(min_length=8)


class ForgotPasswordBody(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class ResetPasswordBody(BaseModel):
    token: str = Field(min_length=8)
    new_password: str = Field(min_length=1)


class ChangePasswordBody(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=1)


class InviteUserBody(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: str = Field(pattern="^(admin|operator|viewer)$")
    first_name: str = Field(default="", max_length=80)
    last_name: str = Field(default="", max_length=80)


class InviteUserResponse(BaseModel):
    user_id: str
    invite_url: str
    mail_ok: bool
    mail_note: str


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


def _set_refresh_cookie(response: Response, token: str, remember: bool, ttl_seconds: int) -> None:
    # Secure flag from env — on for real HTTPS deployments, off in local HTTP dev.
    import os
    secure = os.environ.get("AUTH_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes", "on"}
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        max_age=ttl_seconds,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path="/")


def _pair_to_body(pair: TokenPair) -> TokenBody:
    return TokenBody(
        access_token=pair.access_token,
        expires_in=pair.access_expires_in,
        refresh_token=pair.refresh_token,
    )


# ---------- endpoints -----------------------------------------------

@router.post("/login", response_model=TokenBody)
async def login(
    body: LoginBody,
    request: Request,
    response: Response,
    conn: Annotated[AsyncConnection, DbConn],
    svc: Annotated[AuthService, Depends(dep_auth_service)],
):
    ip = _client_ip(request)
    ua = _client_ua(request)
    rid = _request_id(request)
    norm_email = body.email.strip().lower()
    s = svc.s
    for cat, key, limit, window in (
        ("login", f"ip:{ip}", s.rl_login_ip_limit, s.rl_login_ip_window_seconds),
        ("login", f"email:{norm_email}", s.rl_login_email_limit, s.rl_login_email_window_seconds),
    ):
        retry = await check_and_consume(
            category=cat, key=key, limit=limit, window_seconds=window,
            ip_address=ip, user_agent=ua, request_id=rid,
        )
        if retry is not None:
            raise HTTPException(
                status_code=429,
                detail=f"Too many attempts. Try again in {retry}s.",
                headers={"Retry-After": str(retry)},
            )
    pair = await svc.login(
        conn,
        email=body.email,
        password=body.password,
        remember_me=body.remember,
        ip_address=ip,
        user_agent=ua,
        request_id=rid,
    )
    _set_refresh_cookie(
        response, pair.refresh_token,
        remember=body.remember,
        ttl_seconds=svc.s.refresh_remember_me_ttl_seconds if body.remember
        else svc.s.refresh_token_ttl_seconds,
    )
    return _pair_to_body(pair)


@router.post("/refresh", response_model=TokenBody)
async def refresh(
    body: RefreshBody,
    request: Request,
    response: Response,
    conn: Annotated[AsyncConnection, DbConn],
    svc: Annotated[AuthService, Depends(dep_auth_service)],
    cookie_refresh: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
):
    token = body.refresh_token or cookie_refresh
    if not token:
        from .service import RefreshInvalid
        raise RefreshInvalid("no refresh token supplied")
    pair = await svc.refresh(
        conn,
        refresh_token=token,
        ip_address=_client_ip(request),
        user_agent=_client_ua(request),
        request_id=_request_id(request),
    )
    _set_refresh_cookie(
        response, pair.refresh_token,
        remember=False,
        ttl_seconds=svc.s.refresh_token_ttl_seconds,
    )
    return _pair_to_body(pair)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    ctx: Annotated[AuthContext, current_user_dep()],
    conn: Annotated[AsyncConnection, DbConn],
    svc: Annotated[AuthService, Depends(dep_auth_service)],
):
    if ctx.session_id:
        await svc.logout(
            conn, session_id=ctx.session_id, user_id=ctx.user_id,
            ip_address=_client_ip(request), user_agent=_client_ua(request),
            request_id=_request_id(request),
        )
    _clear_refresh_cookie(response)
    return Response(status_code=204)


@router.post("/logout-all", response_model=LogoutAllBody)
async def logout_all(
    request: Request,
    response: Response,
    ctx: Annotated[AuthContext, current_user_dep()],
    conn: Annotated[AsyncConnection, DbConn],
    svc: Annotated[AuthService, Depends(dep_auth_service)],
):
    n = await svc.logout_all(
        conn, user_id=ctx.user_id or "",
        ip_address=_client_ip(request), user_agent=_client_ua(request),
        request_id=_request_id(request),
    )
    _clear_refresh_cookie(response)
    return LogoutAllBody(revoked=n)


@router.get("/me", response_model=MeBody)
async def me(
    ctx: Annotated[AuthContext, current_user_dep()],
    conn: Annotated[AsyncConnection, DbConn],
    svc: Annotated[AuthService, Depends(dep_auth_service)],
):
    payload = await svc.get_me(conn, user_id=ctx.user_id or "")
    profile = (
        ProfileSummaryBody(**payload.profile.__dict__)
        if payload.profile is not None else None
    )
    return MeBody(**{**payload.__dict__, "profile": profile})


@router.patch("/me/dashboard", response_model=MeBody)
async def update_my_dashboard(
    body: DashboardWidgetsBody,
    ctx: Annotated[AuthContext, current_user_dep()],
    conn: Annotated[AsyncConnection, DbConn],
    svc: Annotated[AuthService, Depends(dep_auth_service)],
):
    """Set (or clear) the current user's custom dashboard widget list."""
    import json as _json
    val = _json.dumps(body.widgets) if body.widgets is not None else None
    await conn.execute(
        text("UPDATE users SET dashboard_widgets = CAST(:v AS jsonb), updated_at = now() WHERE id = :id"),
        {"v": val, "id": ctx.user_id or ""},
    )
    payload = await svc.get_me(conn, user_id=ctx.user_id or "")
    profile = (
        ProfileSummaryBody(**payload.profile.__dict__)
        if payload.profile is not None else None
    )
    return MeBody(**{**payload.__dict__, "profile": profile})


@router.get("/jwks")
async def jwks(
    tokens: Annotated[TokenService, Depends(dep_tokens)],
):
    """Not a real JWKS document yet (M6 wires PEM→JWK conversion); returns
    the current signing key's kid + PEM so an external verifier can pin it.
    """
    return {"keys": [tokens.public_key_info()]}


# ---------- password flow -----------------------------------------------

@router.post("/password/forgot", status_code=202)
async def password_forgot(
    body: ForgotPasswordBody,
    request: Request,
    conn: Annotated[AsyncConnection, DbConn],
    svc: Annotated[AuthService, Depends(dep_auth_service)],
):
    """Always 202. Never leaks whether the email exists. When the rate limit
    trips, silently skip the reset work but still return 202 (per spec §B.11)."""
    ip = _client_ip(request)
    ua = _client_ua(request)
    rid = _request_id(request)
    norm_email = body.email.strip().lower()
    s = svc.s
    throttled = False
    for cat, key, limit, window in (
        ("password_forgot", f"email:{norm_email}",
         s.rl_forgot_email_limit, s.rl_forgot_email_window_seconds),
        ("password_forgot", f"ip:{ip}",
         s.rl_forgot_ip_limit, s.rl_forgot_ip_window_seconds),
    ):
        retry = await check_and_consume(
            category=cat, key=key, limit=limit, window_seconds=window,
            ip_address=ip, user_agent=ua, request_id=rid,
        )
        if retry is not None:
            throttled = True
            break
    if not throttled:
        result = await svc.request_password_reset(
            conn, email=body.email, ip_address=ip, user_agent=ua, request_id=rid,
        )
        if result is not None:
            token, email_addr, display = result
            url = f"{app_base_url().rstrip('/')}/reset-password?token={token}"
            ok, note = send_mail(email_addr, password_reset_email(display, url))
            if not ok:
                print(f"[password_forgot] {note}")
    return Response(status_code=202)


@router.post("/password/reset", status_code=204)
async def password_reset(
    body: ResetPasswordBody,
    request: Request,
    conn: Annotated[AsyncConnection, DbConn],
    svc: Annotated[AuthService, Depends(dep_auth_service)],
):
    try:
        _, email_addr, display = await svc.complete_password_reset(
            conn, token=body.token, new_password=body.new_password,
            ip_address=_client_ip(request), user_agent=_client_ua(request),
            request_id=_request_id(request),
        )
    except PasswordInvalid as e:
        # Complexity failure — 400 problem+json via the fallback handler.
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e) or "password does not meet complexity requirements")
    ip = _client_ip(request)
    ok, note = send_mail(email_addr, password_changed_email(display, ip))
    if not ok:
        print(f"[password_reset] {note}")
    return Response(status_code=204)


@router.post("/password/change", status_code=204)
async def password_change(
    body: ChangePasswordBody,
    request: Request,
    ctx: Annotated[AuthContext, current_user_dep()],
    conn: Annotated[AsyncConnection, DbConn],
    svc: Annotated[AuthService, Depends(dep_auth_service)],
):
    try:
        email_addr, display = await svc.change_password(
            conn,
            user_id=ctx.user_id or "",
            current=body.current_password,
            new=body.new_password,
            ip_address=_client_ip(request), user_agent=_client_ua(request),
            request_id=_request_id(request),
        )
    except PasswordInvalid as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e) or "password does not meet complexity requirements")
    ip = _client_ip(request)
    ok, note = send_mail(email_addr, password_changed_email(display, ip))
    if not ok:
        print(f"[password_change] {note}")
    return Response(status_code=204)


# ---------- email verification / invite acceptance ----------------------

@router.post("/email/verify", status_code=204)
async def email_verify(
    body: VerifyEmailBody,
    request: Request,
    conn: Annotated[AsyncConnection, DbConn],
    svc: Annotated[AuthService, Depends(dep_auth_service)],
):
    await svc.verify_email(
        conn, token=body.token,
        ip_address=_client_ip(request), user_agent=_client_ua(request),
        request_id=_request_id(request),
    )
    return Response(status_code=204)


@router.post("/accept-invite", response_model=TokenBody)
async def accept_invite(
    body: AcceptInviteBody,
    request: Request,
    response: Response,
    conn: Annotated[AsyncConnection, DbConn],
    svc: Annotated[AuthService, Depends(dep_auth_service)],
):
    try:
        user_id = await svc.accept_invite(
            conn,
            token=body.token,
            password=body.password,
            first_name=body.first_name,
            last_name=body.last_name,
            ip_address=_client_ip(request), user_agent=_client_ua(request),
            request_id=_request_id(request),
        )
    except PasswordInvalid as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e) or "password does not meet complexity requirements")

    # Fetch role + ver for token issue and log the new user in immediately.
    r = await conn.execute(
        text("SELECT role, ver FROM users WHERE id = :id"),
        {"id": user_id},
    )
    urow = r.first()
    pair = await svc.issue_login(
        conn,
        user_id=user_id,
        role=str(urow.role),
        ver=int(urow.ver),
        user_agent=_client_ua(request),
        ip_address=_client_ip(request),
    )
    _set_refresh_cookie(response, pair.refresh_token, remember=False,
                        ttl_seconds=svc.s.refresh_token_ttl_seconds)
    return _pair_to_body(pair)


# ---------- admin: invite a user ----------------------------------------

INVITE_TTL_DAYS = 7


@router.post("/users", response_model=InviteUserResponse, status_code=201)
async def invite_user(
    body: InviteUserBody,
    request: Request,
    ctx: Annotated[AuthContext, require("user:invite")],
    conn: Annotated[AsyncConnection, DbConn],
    svc: Annotated[AuthService, Depends(dep_auth_service)],
):
    """Admin creates an INVITED user and mails them a single-use link."""
    email = body.email.strip()
    norm = email.lower()

    # Reject duplicate active email up front (nicer error than a UNIQUE crash).
    existing = await conn.execute(
        text("SELECT id, status FROM users WHERE email_normalized = :e AND deleted_at IS NULL"),
        {"e": norm},
    )
    if existing.first() is not None:
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="a user with that email already exists")

    display = f"{body.first_name.strip()} {body.last_name.strip()}".strip() or email
    user_id = new_id()
    invite_id = new_id()
    token = opaque_token(32)
    token_hash = svc._hash_token(token)

    await conn.execute(
        text("""
            INSERT INTO users
              (id, email, email_normalized, first_name, last_name, display_name,
               role, status, must_change_password, ver, failed_login_attempts,
               created_at, updated_at)
            VALUES
              (:id, :em, :norm, :fn, :ln, :dn,
               :role, 'INVITED', false, 1, 0, now(), now())
        """),
        {
            "id": user_id, "em": email, "norm": norm,
            "fn": body.first_name.strip(), "ln": body.last_name.strip(),
            "dn": display, "role": body.role,
        },
    )
    await conn.execute(
        text(f"""
            INSERT INTO invitations
              (id, email, role, token_hash, invited_by_id, expires_at)
            VALUES
              (:id, :em, :role, :h, :by, now() + interval '{INVITE_TTL_DAYS} days')
        """),
        {"id": invite_id, "em": email, "role": body.role, "h": token_hash, "by": ctx.user_id},
    )

    invite_url = f"{app_base_url().rstrip('/')}/accept-invite?token={token}"
    invited_by_label = ctx.user_id or "an administrator"
    ok, note = send_mail(email, invite_email(display, invite_url, invited_by_label, body.role))

    from .audit import AuditEvent, audit_logger
    await audit_logger().emit(
        None, ctx,
        AuditEvent(action="USER_INVITED", target_type="user", target_id=user_id,
                   context={"email": email, "role": body.role, "mail_ok": ok}),
        actor_ip=_client_ip(request), user_agent=_client_ua(request),
    )
    from .notifications import NotificationEvent, emit_for_category
    await emit_for_category(NotificationEvent(
        category="user_lifecycle", kind="user_invited",
        title="User invited",
        body=f"{email} was invited as {body.role}.",
        url="/admin/users",
        context={"user_id": user_id, "role": body.role, "mail_ok": ok},
    ))
    return InviteUserResponse(
        user_id=user_id, invite_url=invite_url, mail_ok=ok, mail_note=note,
    )
