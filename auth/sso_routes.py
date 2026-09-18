"""/api/v1/auth/sso router — Microsoft Entra SSO (docs/ENTRA_SSO.md §4, §6).

Backend-driven OIDC. Thin orchestration only: EntraSso.begin/complete →
AuthService.resolve_sso_user → AuthService.issue_login → set the same
cfv_refresh cookie /login sets → 302 to /. The frontend's existing
silent-refresh bootstrap turns that cookie into an in-memory access token.

  GET /sso/config    public — { enabled: bool } so the login page can show/hide the button
  GET /sso/login     302 → Entra authorize (sets the signed state cookie)
  GET /sso/callback  verify state → exchange code → verify id_token → resolve → issue_login → 302 /
  GET /sso/logout    revoke the CFV session → 302 → Entra end_session (RP-initiated logout)

When SSO is disabled, /login and /callback return 404 (FR-SSO-11); /config
still answers so the frontend just hides the button.
"""
from __future__ import annotations

import logging
from typing import Annotated

import anyio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncConnection

from . import app_settings
from .audit import AuditEvent, audit_logger
from .context import AuthContext
from .deps import DbConn, dep_auth_service, dep_mfa_service
from .mfa import CHALLENGE_TTL as MFA_CHALLENGE_TTL, MfaService
from .routes import (
    REFRESH_COOKIE,
    _clear_refresh_cookie,
    _client_ip,
    _client_ua,
    _request_id,
    _set_mfa_cookie,
    _set_refresh_cookie,
)
from .service import AccountDisabled, AuthService
from .sso import EntraSso, SsoError

router = APIRouter(prefix="/api/v1/auth/sso", tags=["sso"])

SSO_STATE_COOKIE = "cfv_sso"
# ponytail: plain cookie name in dev (HTTP); the __Host- prefix wants Secure +
# Path=/ + no Domain and only applies under HTTPS — same posture as cfv_refresh.


def _cookie_secure() -> bool:
    import os
    return os.environ.get("AUTH_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes", "on"}


def _apply_office_location(user_id: str, office: str) -> None:
    """Sync-db office→location assignment, run in a worker thread from the callback."""
    from db import connect, set_user_location_from_office
    set_user_location_from_office(connect(), user_id, office)


def _set_state_cookie(resp: RedirectResponse, value: str, ttl: int) -> None:
    resp.set_cookie(
        key=SSO_STATE_COOKIE, value=value, max_age=ttl,
        httponly=True, secure=_cookie_secure(), samesite="lax", path="/",
    )


def _clear_state_cookie(resp: RedirectResponse) -> None:
    resp.delete_cookie(SSO_STATE_COOKIE, path="/")


def _login_redirect(reason: str | None = None) -> RedirectResponse:
    url = f"/login?sso_error={reason}" if reason else "/login"
    resp = RedirectResponse(url=url, status_code=302)
    _clear_state_cookie(resp)
    return resp


async def _audit_fail(request: Request, reason: str) -> None:
    await audit_logger().emit(
        None,
        AuthContext(actor_kind="anonymous", request_id=_request_id(request)),
        AuditEvent(action="SSO_LOGIN_FAILURE", outcome="failure", severity="SEC",
                   context={"reason": reason}),
        actor_ip=_client_ip(request), user_agent=_client_ua(request),
    )


async def _sso_effective(conn: AsyncConnection, svc: AuthService) -> bool:
    """SSO is usable only when it's configured in env AND the runtime toggle
    (`sso_login_enabled`, flipped from the Settings page) is on."""
    if not svc.s.sso_enabled:
        return False
    return await app_settings.get_bool(conn, "sso_login_enabled")


@router.get("/config")
async def sso_config(
    conn: Annotated[AsyncConnection, DbConn],
    svc: Annotated[AuthService, Depends(dep_auth_service)],
):
    return {"enabled": await _sso_effective(conn, svc)}


@router.get("/logout")
async def sso_logout(
    request: Request,
    conn: Annotated[AsyncConnection, DbConn],
    svc: Annotated[AuthService, Depends(dep_auth_service)],
):
    """RP-initiated logout. Ends the CFV session *and* Entra's, so the next
    "Sign in with Microsoft" asks for credentials instead of silently reusing
    Microsoft's session cookie (matters on the shared lab PCs).

    A plain GET the browser navigates to — the redirect to Entra has to be a
    top-level navigation, so this can't be the existing POST /logout. It is
    safe as a GET: worst case an attacker logs someone out.

    The frontend calls POST /auth/logout first; revoking here too keeps the
    endpoint correct when hit directly (e.g. a bookmarked sign-out link).
    """
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        row = await svc.sessions.find_by_refresh_hash(
            conn, svc.tokens.hash_refresh(token))
        if row is not None and row.revoked_at is None:
            await svc.logout(
                conn, session_id=row.id, user_id=row.user_id,
                ip_address=_client_ip(request), user_agent=_client_ua(request),
                request_id=_request_id(request),
            )

    if not svc.s.sso_enabled:
        # SSO off — nothing to sign out of upstream, just land on /login.
        return _login_redirect()

    resp = RedirectResponse(url=EntraSso(svc.s).logout_url(), status_code=302)
    _clear_refresh_cookie(resp)
    _clear_state_cookie(resp)
    return resp


@router.get("/login")
async def sso_login(
    conn: Annotated[AsyncConnection, DbConn],
    svc: Annotated[AuthService, Depends(dep_auth_service)],
):
    if not svc.s.sso_enabled:
        raise HTTPException(status_code=404)
    if not await app_settings.get_bool(conn, "sso_login_enabled"):
        # SSO configured but turned off by an admin → send them to local login.
        return _login_redirect("sso_off")
    begin = EntraSso(svc.s).begin()
    resp = RedirectResponse(url=begin.authorize_url, status_code=302)
    _set_state_cookie(resp, begin.state_cookie, ttl=svc.s.sso_state_ttl_seconds)
    return resp


@router.get("/callback")
async def sso_callback(
    request: Request,
    conn: Annotated[AsyncConnection, DbConn],
    svc: Annotated[AuthService, Depends(dep_auth_service)],
    mfa: Annotated[MfaService, Depends(dep_mfa_service)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if not svc.s.sso_enabled:
        raise HTTPException(status_code=404)
    if not await app_settings.get_bool(conn, "sso_login_enabled"):
        # Toggle flipped off mid-flow — refuse before touching the code.
        await _audit_fail(request, "sso_off")
        return _login_redirect("sso_off")

    # Entra bounced back with an error (e.g. user cancelled consent).
    if error:
        await _audit_fail(request, "cancelled")
        return _login_redirect("cancelled")
    if not code or not state:
        await _audit_fail(request, "state")
        return _login_redirect("state")

    entra = EntraSso(svc.s)
    state_cookie = request.cookies.get(SSO_STATE_COOKIE)
    ip, ua, rid = _client_ip(request), _client_ua(request), _request_id(request)

    try:
        claims = await entra.complete(code=code, state=state, state_cookie=state_cookie)
        # Allowlist gate (Settings → Access & Security). Applies to every SSO
        # sign-in, new or existing; local /admin/login accounts are unaffected.
        if not await app_settings.is_sso_allowed(conn, claims.email):
            await _audit_fail(request, "not_allowed")
            return _login_redirect("not_allowed")
        uid, role, ver = await svc.resolve_sso_user(
            conn, claims=claims, ip_address=ip, user_agent=ua, request_id=rid)
        # Assign the user's office from their O365 "Office" field (best-effort;
        # only if unset, never clobbering an admin override). See MULTI_LOCATION.md.
        if claims.office:
            try:
                await anyio.to_thread.run_sync(_apply_office_location, uid, claims.office)
            except Exception:
                logging.getLogger("cfv.sso").warning("office→location sync failed", exc_info=True)
        outcome = await svc.finish_primary_auth(
            conn, user_id=uid, role=role, ver=ver, remember=False,
            ip_address=ip, user_agent=ua, request_id=rid, mfa=mfa)
    except SsoError as e:
        # Full detail to the server log (never to the user); coarse reason to audit.
        logging.getLogger("cfv.sso").warning("SSO callback failed [%s]: %s", e.reason, e)
        await _audit_fail(request, e.reason)
        return _login_redirect(e.reason)
    except AccountDisabled:
        logging.getLogger("cfv.sso").warning("SSO callback denied: account disabled")
        await _audit_fail(request, "disabled")
        return _login_redirect("disabled")

    # MFA needed → park on the challenge and send the browser to /mfa. The
    # signed challenge rides an HttpOnly cookie; the /mfa page completes it.
    if outcome.mfa is not None:
        resp = RedirectResponse(url="/mfa", status_code=302)
        _set_mfa_cookie(resp, mfa.sign_cookie(outcome.mfa.challenge_id),
                        ttl_seconds=MFA_CHALLENGE_TTL)
        _clear_state_cookie(resp)
        return resp

    # No MFA — hand the browser the same refresh cookie /login sets, then land
    # on the app; the auth-provider bootstrap silent-refreshes to get the access
    # token. No token is ever placed in the URL.
    pair = outcome.pair
    resp = RedirectResponse(url="/", status_code=302)
    _set_refresh_cookie(resp, pair.refresh_token, remember=False,
                        ttl_seconds=svc.s.refresh_token_ttl_seconds)
    _clear_state_cookie(resp)
    return resp
