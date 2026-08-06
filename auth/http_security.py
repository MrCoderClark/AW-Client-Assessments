"""Security headers + double-submit CSRF middleware.

ponytail: two thin ASGI middlewares, no framework beyond starlette. Both
opt out cleanly for docs and health endpoints, and CSRF opts out for
Bearer-authed calls (their token IS the anti-CSRF).
"""
from __future__ import annotations

import hmac
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

CSRF_COOKIE = "cfv_csrf"
CSRF_HEADER = "x-csrf-token"

# Paths where CSRF verification is skipped even in cookie mode.
#   - /api/v1/auth/*: either pre-auth (login/refresh — bootstrap problem, no
#     cookie exists yet) or out-of-band token flows (reset/verify/invite).
#     Post-auth endpoints under this prefix (/me, /password/change, /logout)
#     use Bearer via apiFetch and are already skipped by the header check.
#   - OpenAPI docs: Try-it-out flows don't set up the cookie handshake.
#   - /api/health: unauthenticated diagnostic.
CSRF_EXEMPT_PATHS = (
    "/api/health",
    "/api/openapi.json",
    "/api/docs",
    "/api/docs/oauth2-redirect",
    "/api/redoc",
    "/api/v1/auth",
)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _cookie_secure() -> bool:
    v = os.environ.get("AUTH_COOKIE_SECURE", "").strip().lower()
    return v in {"1", "true", "yes", "on"}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add the standard hardening headers to every response.

    ponytail: CSP allows `unsafe-inline` for style/script because Next.js
    injects inline styles at hydration; tightening to nonce-based CSP is
    an M6 hardening pass, not this branch. blob: on img-src is required
    for the PDF viewer's fetch→blob→iframe flow.
    """

    HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        "Content-Security-Policy": (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "font-src 'self' data:; "
            "object-src 'none'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        ),
    }

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        for k, v in self.HEADERS.items():
            response.headers.setdefault(k, v)
        # HSTS only over HTTPS — meaningless (and mis-advised) on plain HTTP.
        scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
        if scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains",
            )
        return response


class CsrfMiddleware(BaseHTTPMiddleware):
    """Double-submit CSRF for cookie-mode requests.

    Rules:
      - Safe methods (GET/HEAD/OPTIONS): pass through, and stamp a csrf
        cookie if the caller doesn't have one yet (readable by JS so the
        SPA can echo it back on unsafe methods).
      - Unsafe methods: skip when Authorization: Bearer is present (that
        header itself proves the request was made by JS on our origin — a
        CSRF-vulnerable browser flow can't set arbitrary headers cross-
        origin without an explicit CORS preflight).
      - Otherwise (cookie-only auth), require the X-CSRF-Token header to
        constant-time-equal the cfv_csrf cookie value.
    """

    async def dispatch(self, request: Request, call_next):
        method = request.method.upper()
        path = request.url.path

        if any(path == p or path.startswith(p + "/") for p in CSRF_EXEMPT_PATHS):
            response: Response = await call_next(request)
            self._ensure_cookie(request, response)
            return response

        if method in SAFE_METHODS:
            response = await call_next(request)
            self._ensure_cookie(request, response)
            return response

        # Unsafe method — either Bearer-authed (skip) or requires token.
        if request.headers.get("authorization", "").lower().startswith("bearer "):
            response = await call_next(request)
            self._ensure_cookie(request, response)
            return response

        cookie = request.cookies.get(CSRF_COOKIE)
        header = request.headers.get(CSRF_HEADER)
        if not cookie or not header or not hmac.compare_digest(cookie, header):
            return JSONResponse(
                status_code=403,
                media_type="application/problem+json",
                content={
                    "type": "https://cfv/errors/csrf",
                    "title": "CSRF token missing or invalid",
                    "status": 403,
                    "detail": "Cookie-mode requests require a matching X-CSRF-Token header.",
                    "instance": path,
                    "code": "CSRF_FAILED",
                },
            )

        response = await call_next(request)
        self._ensure_cookie(request, response)
        return response

    @staticmethod
    def _ensure_cookie(request: Request, response: Response) -> None:
        if request.cookies.get(CSRF_COOKIE):
            return
        response.set_cookie(
            key=CSRF_COOKIE,
            value=secrets.token_urlsafe(32),
            max_age=60 * 60 * 24 * 30,
            httponly=False,   # readable by JS on purpose (double-submit)
            secure=_cookie_secure(),
            samesite="lax",
            path="/",
        )
