"""Microsoft Entra SSO — OIDC Authorization Code + PKCE adapter.

Backend-driven (docs/ENTRA_SSO.md §3): FastAPI owns the whole OIDC dance and
mints the app's *own* session at the end via AuthService.issue_login. Entra is
used only to prove identity once, at the callback.

This module is deliberately split into pure module-level functions (PKCE, the
signed state cookie, the authorize URL, id_token verification) plus a thin
`EntraSso` class that wires them to settings + live HTTP. The pure parts take
their inputs explicitly so the smoke can exercise them offline with a locally
minted token + key — no live tenant required.

No new backend deps: PyJWT[crypto] verifies the id_token, httpx does the code
exchange (same primitives already used for JWT access tokens + HIBP).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, replace
from functools import cached_property
from typing import Any
from urllib.parse import urlencode

import anyio
import httpx
import jwt as _jwt

from .random import const_eq, new_id, opaque_token
from .settings import AuthSettings

ID_TOKEN_ALGS = ["RS256"]  # Entra signs id_tokens with RS256


class SsoError(Exception):
    """Any SSO failure. `reason` is a coarse, non-leaky bucket for the
    /login?sso_error= redirect + the audit row (state|token|disabled|config).
    """

    def __init__(self, message: str, *, reason: str = "token") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class SsoClaims:
    subject: str            # Entra `oid` — immutable object id
    tenant: str             # Entra `tid`
    email: str
    display_name: str | None
    first_name: str | None
    last_name: str | None
    office: str | None = None   # O365 "Office" (Graph officeLocation) → location mapping


# ---------- base64url helpers ---------------------------------------

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# ---------- PKCE ----------------------------------------------------

def pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256.

    Verifier is 43-128 chars of URL-safe randomness; challenge is
    base64url(sha256(verifier)) with padding stripped.
    """
    verifier = opaque_token(64)  # ~86 url-safe chars, within the RFC 7636 range
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


# ---------- signed state cookie -------------------------------------
# Carries state + nonce + PKCE verifier across the redirect to Entra and back,
# HMAC-signed with the existing refresh-hash secret so a forged callback can't
# smuggle an attacker-chosen verifier/nonce (SEC-SSO-03).

def sign_state_cookie(payload: dict[str, Any], secret: bytes) -> str:
    body = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def read_state_cookie(value: str, secret: bytes, *, now: int | None = None) -> dict[str, Any]:
    """Verify signature + expiry, return the payload. Raise SsoError(reason=state) otherwise."""
    try:
        body, sig = value.split(".", 1)
    except ValueError:
        raise SsoError("malformed state cookie", reason="state")
    expected = hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()
    if not const_eq(sig, expected):
        raise SsoError("state cookie signature mismatch", reason="state")
    try:
        payload = json.loads(_b64url_decode(body))
    except (ValueError, json.JSONDecodeError):
        raise SsoError("state cookie payload undecodable", reason="state")
    exp = int(payload.get("exp", 0))
    if (now or int(time.time())) >= exp:
        raise SsoError("state cookie expired", reason="state")
    return payload


# ---------- authorize URL -------------------------------------------

def build_authorize_url(
    *,
    authorize_endpoint: str,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str,
    nonce: str,
    code_challenge: str,
) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": scope,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{authorize_endpoint}?{urlencode(params)}"


# ---------- end-session (RP-initiated logout) URL -------------------

def build_logout_url(*, logout_endpoint: str, post_logout_redirect_uri: str) -> str:
    """OIDC RP-initiated logout. Entra clears its own session cookie and then
    bounces the browser to `post_logout_redirect_uri` — which must be listed as
    a post-logout redirect URI on the app registration, or Entra shows its
    generic "you're signed out" page instead of redirecting.

    `id_token_hint` is deliberately omitted: we discard the id_token after
    verification (nothing stored, nothing to hint with). Entra then prompts
    "pick an account to sign out of" when multiple sessions exist, which is the
    right behavior on a shared lab PC anyway.
    """
    params = {"post_logout_redirect_uri": post_logout_redirect_uri}
    return f"{logout_endpoint}?{urlencode(params)}"


# ---------- id_token verification -----------------------------------

def _extract_email(claims: dict[str, Any]) -> str | None:
    for k in ("email", "preferred_username", "upn"):
        v = claims.get(k)
        if v and "@" in str(v):
            return str(v)
    return None


def verify_id_token(
    token: str,
    *,
    signing_key: Any,
    issuer: str,
    audience: str,
    nonce: str,
    tenant_id: str,
    leeway: int = 120,
) -> SsoClaims:
    """Validate signature + iss/aud/exp/nbf + nonce + tenant, return SsoClaims.

    `signing_key` is the key object resolved from the token's `kid` (in prod via
    PyJWKClient against the tenant JWKS; in the smoke, a locally held public key).
    """
    try:
        claims: dict[str, Any] = _jwt.decode(
            token,
            signing_key,
            algorithms=ID_TOKEN_ALGS,
            audience=audience,
            issuer=issuer,
            leeway=leeway,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
    except _jwt.PyJWTError as e:
        raise SsoError(f"id_token invalid: {e}", reason="token") from e

    # nonce binds the token to *our* authorize request (replay defense).
    if not const_eq(str(claims.get("nonce", "")), nonce):
        raise SsoError("id_token nonce mismatch", reason="token")

    # Single-tenant lock (SEC-SSO-06): reject other tenants even if signed.
    tid = str(claims.get("tid", ""))
    if not const_eq(tid, tenant_id):
        raise SsoError("id_token tenant mismatch", reason="token")

    oid = claims.get("oid")
    if not oid:
        raise SsoError("id_token missing oid", reason="token")

    email = _extract_email(claims)
    if not email:
        raise SsoError("id_token has no usable email", reason="token")

    return SsoClaims(
        subject=str(oid),
        tenant=tid,
        email=email,
        display_name=(claims.get("name") or None),
        first_name=(claims.get("given_name") or None),
        last_name=(claims.get("family_name") or None),
    )


# ---------- the adapter --------------------------------------------

@dataclass(frozen=True)
class SsoBegin:
    authorize_url: str
    state_cookie: str


class EntraSso:
    """Wires the pure helpers above to settings + live Entra HTTP."""

    def __init__(self, settings: AuthSettings) -> None:
        self.s = settings

    @property
    def enabled(self) -> bool:
        return self.s.sso_enabled

    # ---- endpoint derivation ----
    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.s.entra_tenant_id}"

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.s.entra_tenant_id}/v2.0"

    @property
    def authorize_endpoint(self) -> str:
        return f"{self.authority}/oauth2/v2.0/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"{self.authority}/oauth2/v2.0/token"

    @property
    def jwks_uri(self) -> str:
        return f"{self.authority}/discovery/v2.0/keys"

    @property
    def logout_endpoint(self) -> str:
        return f"{self.authority}/oauth2/v2.0/logout"

    @property
    def redirect_uri(self) -> str:
        if self.s.entra_redirect_uri:
            return self.s.entra_redirect_uri
        from .emails import app_base_url
        return f"{app_base_url().rstrip('/')}/api/v1/auth/sso/callback"

    @property
    def post_logout_redirect_uri(self) -> str:
        if self.s.entra_post_logout_redirect_uri:
            return self.s.entra_post_logout_redirect_uri
        from .emails import app_base_url
        return f"{app_base_url().rstrip('/')}/login"

    def logout_url(self) -> str:
        return build_logout_url(
            logout_endpoint=self.logout_endpoint,
            post_logout_redirect_uri=self.post_logout_redirect_uri,
        )

    @cached_property
    def _jwks_client(self) -> _jwt.PyJWKClient:
        # PyJWKClient caches keys and re-fetches on an unknown kid (rotation).
        return _jwt.PyJWKClient(self.jwks_uri)

    # ---- step 1: /sso/login ----
    def begin(self) -> SsoBegin:
        """Mint state+nonce+PKCE, return the authorize URL and the signed
        state cookie value the route should set."""
        state = opaque_token(32)
        nonce = opaque_token(32)
        verifier, challenge = pkce_pair()
        cookie = sign_state_cookie(
            {
                "state": state,
                "nonce": nonce,
                "verifier": verifier,
                "exp": int(time.time()) + self.s.sso_state_ttl_seconds,
                "jti": new_id(),
            },
            self.s.refresh_hash_secret,
        )
        url = build_authorize_url(
            authorize_endpoint=self.authorize_endpoint,
            client_id=self.s.entra_client_id,
            redirect_uri=self.redirect_uri,
            scope=self.s.entra_scopes,
            state=state,
            nonce=nonce,
            code_challenge=challenge,
        )
        return SsoBegin(authorize_url=url, state_cookie=cookie)

    # ---- step 2: /sso/callback ----
    async def complete(self, *, code: str, state: str, state_cookie: str | None) -> SsoClaims:
        """Verify state, exchange the code, verify the id_token, return claims."""
        if not state_cookie:
            raise SsoError("missing state cookie", reason="state")
        payload = read_state_cookie(state_cookie, self.s.refresh_hash_secret)
        if not const_eq(str(payload.get("state", "")), state):
            raise SsoError("state mismatch", reason="state")

        id_token, access_token = await self._exchange_code(code=code, verifier=str(payload["verifier"]))

        # PyJWKClient.get_signing_key_from_jwt does a BLOCKING urllib fetch, and
        # verify is CPU work — both must run off the event loop. On the loop they
        # freeze every other request (health included) and, if the request is
        # cancelled mid-fetch, its DB connection leaks (pool exhaustion). Offload
        # to a worker thread so it's a proper await/cancellation point.
        nonce = str(payload["nonce"])

        def _verify_sync() -> SsoClaims:
            signing_key = self._jwks_client.get_signing_key_from_jwt(id_token).key
            return verify_id_token(
                id_token,
                signing_key=signing_key,
                issuer=self.issuer,
                audience=self.s.entra_client_id,
                nonce=nonce,
                tenant_id=self.s.entra_tenant_id,
            )

        claims = await anyio.to_thread.run_sync(_verify_sync)
        # Best-effort: enrich with the O365 "Office" field (Graph) so the caller
        # can map it to a location. No-ops to None if User.Read isn't consented.
        office = await self._fetch_office(access_token)
        return replace(claims, office=office)

    async def _fetch_office(self, access_token: str) -> str | None:
        """The user's O365 Office (Graph `officeLocation`), best-effort.

        Returns None unless User.Read is consented and the access token is
        Graph-capable — so location auto-assignment stays dormant until the
        scope is granted in Entra, then lights up with no code change.
        """
        if not access_token:
            return None
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(
                    "https://graph.microsoft.com/v1.0/me?$select=officeLocation",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            if r.status_code == 200:
                val = r.json().get("officeLocation")
                return str(val).strip() if val else None
        except httpx.HTTPError:
            pass
        return None

    async def _exchange_code(self, *, code: str, verifier: str) -> tuple[str, str]:
        data = {
            "client_id": self.s.entra_client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": verifier,
            "client_secret": self.s.entra_client_secret,
            "scope": self.s.entra_scopes,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self.token_endpoint, data=data)
        except httpx.HTTPError as e:
            raise SsoError(f"token endpoint unreachable: {e}", reason="token") from e
        if resp.status_code != 200:
            # Entra returns {error, error_description, error_codes}. Fold the
            # AADSTS detail into the exception message for SERVER-SIDE logging
            # only — the route never shows this to the user.
            detail = ""
            try:
                j = resp.json()
                desc = str(j.get("error_description", "")).splitlines()
                detail = f" ({j.get('error')}: {desc[0] if desc else ''})"
            except Exception:
                detail = f" (body: {resp.text[:300]})"
            raise SsoError(f"token exchange failed: {resp.status_code}{detail}", reason="token")
        body = resp.json()
        id_token = body.get("id_token")
        if not id_token:
            raise SsoError("token response missing id_token", reason="token")
        # access_token drives a best-effort Graph /me officeLocation lookup for
        # location mapping; empty / non-Graph if User.Read isn't consented.
        return str(id_token), str(body.get("access_token") or "")
