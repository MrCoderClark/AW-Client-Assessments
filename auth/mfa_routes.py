"""/api/v1/auth/mfa router — complete a pending MFA challenge.

Reached after primary auth (password login or SSO callback) parked the user on
an MFA challenge (HttpOnly cookie `cfv_mfa`). No access token is needed to call
these — the signed challenge cookie is the credential. On success the same
`cfv_refresh` cookie /login sets is issued and the challenge is cleared.

  GET  /mfa/challenge      { purpose }  — verify | enroll
  POST /mfa/enroll/start   → { secret, otpauth_uri }   (purpose=enroll)
  POST /mfa/enroll/verify  { code } → session + { backup_codes }
  POST /mfa/email/send     → { ok }   (verify: email a one-time code)
  POST /mfa/verify         { code, method } → session   (verify)

Under /api/v1/auth → inherits the CSRF exemption (no access token yet to carry
a CSRF token, and the cookie is not a bearer credential).
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from . import totp
from .audit import AuditEvent, audit_logger
from .context import AuthContext
from .deps import DbConn, dep_auth_service, dep_mfa_service
from .mfa import Challenge, ChallengeInvalid, CodeInvalid, MfaService
from .routes import (
    MFA_COOKIE,
    _clear_mfa_cookie,
    _client_ip,
    _client_ua,
    _request_id,
    _set_refresh_cookie,
)
from .service import AuthService

router = APIRouter(prefix="/api/v1/auth/mfa", tags=["mfa"])

MfaCookie = Annotated[str | None, Cookie(alias=MFA_COOKIE)]


# ---------- models --------------------------------------------------

class ChallengeInfo(BaseModel):
    purpose: str


class EnrollStart(BaseModel):
    secret: str
    otpauth_uri: str


class CodeBody(BaseModel):
    code: str = Field(min_length=1, max_length=32)


class VerifyBody(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    method: Literal["totp", "email", "backup"] = "totp"


class MfaResult(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    refresh_token: str
    backup_codes: list[str] | None = None


# ---------- helpers -------------------------------------------------

async def _challenge(conn: AsyncConnection, mfa: MfaService, cookie: str | None) -> Challenge:
    if not cookie:
        raise HTTPException(status_code=401, detail="no MFA challenge")
    try:
        cid = mfa.read_cookie(cookie)
        return await mfa.load(conn, cid)
    except ChallengeInvalid:
        raise HTTPException(status_code=401, detail="MFA challenge expired — sign in again")


async def _record_failure(mfa: MfaService, conn: AsyncConnection, cid: str) -> None:
    """Map a wrong-code failure to the right status (always raises)."""
    try:
        await mfa.record_failure(conn, cid)
    except ChallengeInvalid:
        raise HTTPException(status_code=401, detail="too many attempts — sign in again")
    except CodeInvalid:
        raise HTTPException(status_code=400, detail="incorrect code")


async def _complete(
    conn: AsyncConnection, response: Response, request: Request,
    svc: AuthService, mfa: MfaService, ch: Challenge,
    backup_codes: list[str] | None = None,
) -> MfaResult:
    """Verification passed → issue the real session and clear the challenge."""
    r = await conn.execute(text("SELECT role, ver FROM users WHERE id = :id"), {"id": ch.user_id})
    u = r.first()
    pair = await svc.issue_login(
        conn, user_id=ch.user_id, role=str(u.role), ver=int(u.ver),
        ip_address=_client_ip(request), user_agent=_client_ua(request),
        remember_me=ch.remember,
    )
    await mfa.delete_challenge(conn, ch.id)
    await audit_logger().emit(
        None, AuthContext(actor_kind="user", user_id=ch.user_id, request_id=_request_id(request)),
        AuditEvent(action="AUTH_LOGIN_SUCCESS", target_type="user", target_id=ch.user_id,
                   context={"session_id": pair.session_id, "mfa": True}),
        actor_ip=_client_ip(request), user_agent=_client_ua(request),
    )
    ttl = (svc.s.refresh_remember_me_ttl_seconds if ch.remember
           else svc.s.refresh_token_ttl_seconds)
    _set_refresh_cookie(response, pair.refresh_token, remember=ch.remember, ttl_seconds=ttl)
    _clear_mfa_cookie(response)
    return MfaResult(access_token=pair.access_token, expires_in=pair.access_expires_in,
                     refresh_token=pair.refresh_token, backup_codes=backup_codes)


# ---------- endpoints -----------------------------------------------

@router.get("/challenge", response_model=ChallengeInfo)
async def challenge_info(
    conn: Annotated[AsyncConnection, DbConn],
    mfa: Annotated[MfaService, Depends(dep_mfa_service)],
    cookie: MfaCookie = None,
):
    ch = await _challenge(conn, mfa, cookie)
    return ChallengeInfo(purpose=ch.purpose)


@router.post("/enroll/start", response_model=EnrollStart)
async def enroll_start(
    conn: Annotated[AsyncConnection, DbConn],
    mfa: Annotated[MfaService, Depends(dep_mfa_service)],
    cookie: MfaCookie = None,
):
    ch = await _challenge(conn, mfa, cookie)
    if ch.purpose != "enroll":
        raise HTTPException(status_code=400, detail="not an enrolment challenge")
    r = await conn.execute(text("SELECT email FROM users WHERE id = :id"), {"id": ch.user_id})
    row = r.first()
    secret, uri = await mfa.enroll_start(conn, ch.id, account_email=str(row.email))
    return EnrollStart(secret=secret, otpauth_uri=uri)


@router.post("/enroll/verify", response_model=MfaResult)
async def enroll_verify(
    body: CodeBody,
    request: Request,
    response: Response,
    conn: Annotated[AsyncConnection, DbConn],
    svc: Annotated[AuthService, Depends(dep_auth_service)],
    mfa: Annotated[MfaService, Depends(dep_mfa_service)],
    cookie: MfaCookie = None,
):
    ch = await _challenge(conn, mfa, cookie)
    if ch.purpose != "enroll":
        raise HTTPException(status_code=400, detail="not an enrolment challenge")
    secret = await mfa.pending_secret(conn, ch.id)
    if not secret:
        raise HTTPException(status_code=400, detail="start enrolment first")
    if not totp.verify(secret, body.code):
        await _record_failure(mfa, conn, ch.id)
    backup_codes = await mfa.persist_totp(conn, ch.user_id, secret)
    await audit_logger().emit(
        None, AuthContext(actor_kind="user", user_id=ch.user_id, request_id=_request_id(request)),
        AuditEvent(action="AUTH_MFA_ENROLLED", severity="SEC",
                   target_type="user", target_id=ch.user_id),
        actor_ip=_client_ip(request), user_agent=_client_ua(request),
    )
    return await _complete(conn, response, request, svc, mfa, ch, backup_codes=backup_codes)


@router.post("/email/send")
async def email_send(
    conn: Annotated[AsyncConnection, DbConn],
    mfa: Annotated[MfaService, Depends(dep_mfa_service)],
    cookie: MfaCookie = None,
):
    ch = await _challenge(conn, mfa, cookie)
    if ch.purpose != "verify":
        raise HTTPException(status_code=400, detail="email codes are for verification only")
    r = await conn.execute(
        text("SELECT email, display_name, first_name FROM users WHERE id = :id"), {"id": ch.user_id})
    row = r.first()
    ok, _note = await mfa.send_email_code(
        conn, ch.id, email=str(row.email), name=(row.display_name or row.first_name))
    # Generic response — never reveal delivery detail to an unauthenticated caller.
    return {"ok": bool(ok)}


@router.post("/verify", response_model=MfaResult)
async def verify(
    body: VerifyBody,
    request: Request,
    response: Response,
    conn: Annotated[AsyncConnection, DbConn],
    svc: Annotated[AuthService, Depends(dep_auth_service)],
    mfa: Annotated[MfaService, Depends(dep_mfa_service)],
    cookie: MfaCookie = None,
):
    ch = await _challenge(conn, mfa, cookie)
    if ch.purpose != "verify":
        raise HTTPException(status_code=400, detail="this account must finish enrolment")
    if body.method == "totp":
        ok = await mfa.verify_totp(conn, ch.user_id, body.code)
    elif body.method == "email":
        ok = await mfa.verify_email_code(conn, ch.id, body.code)
    else:  # backup
        ok = await mfa.verify_backup(conn, ch.user_id, body.code)
    if not ok:
        await _record_failure(mfa, conn, ch.id)
    return await _complete(conn, response, request, svc, mfa, ch)
