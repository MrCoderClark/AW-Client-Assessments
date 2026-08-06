"""RFC 9457 Problem Details response shape + FastAPI exception handlers.

Everything the auth surface raises passes through here so responses have a
uniform shape:
  { type, title, status, detail, instance, code }
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .passwords import PasswordComplexityError
from .service import (
    AccountLocked,
    AuthError,
    InvalidCredentials,
    InvalidToken,
    PasswordInvalid,
    RefreshInvalid,
    SessionRevoked,
)

PROBLEM_MEDIA = "application/problem+json"
BASE = "https://cfv/errors"


def _problem(
    status: int,
    *,
    code: str,
    title: str,
    detail: str,
    instance: str,
    extra: dict | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict = {
        "type": f"{BASE}/{code.lower().replace('_', '-')}",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
        "code": code,
    }
    if extra:
        body.update(extra)
    return JSONResponse(
        status_code=status, content=body, media_type=PROBLEM_MEDIA, headers=headers,
    )


def register(app: FastAPI) -> None:
    @app.exception_handler(InvalidCredentials)
    async def _invalid_creds(request: Request, exc: InvalidCredentials):
        return _problem(
            401, code="AUTH_INVALID_CREDENTIALS",
            title="Invalid credentials",
            detail=str(exc) or "The credentials provided are not valid.",
            instance=request.url.path,
        )

    @app.exception_handler(AccountLocked)
    async def _locked(request: Request, exc: AccountLocked):
        return _problem(
            423, code="AUTH_ACCOUNT_LOCKED",
            title="Account locked",
            detail=str(exc) or "This account is temporarily locked.",
            instance=request.url.path,
        )

    @app.exception_handler(RefreshInvalid)
    async def _refresh(request: Request, exc: RefreshInvalid):
        return _problem(
            401, code="AUTH_REFRESH_INVALID",
            title="Refresh token invalid",
            detail=str(exc) or "Refresh token is not accepted.",
            instance=request.url.path,
        )

    @app.exception_handler(SessionRevoked)
    async def _session(request: Request, exc: SessionRevoked):
        return _problem(
            401, code="AUTH_SESSION_REVOKED",
            title="Session revoked",
            detail=str(exc) or "This session is no longer valid.",
            instance=request.url.path,
        )

    @app.exception_handler(InvalidToken)
    async def _token(request: Request, exc: InvalidToken):
        return _problem(
            400, code="AUTH_INVALID_TOKEN",
            title="Invalid or expired link",
            detail=str(exc) or "This link is not valid or has expired.",
            instance=request.url.path,
        )

    @app.exception_handler(PasswordInvalid)
    async def _pw(request: Request, exc: PasswordInvalid):
        return _problem(
            400, code="AUTH_PASSWORD_INVALID",
            title="Password not accepted",
            detail=str(exc) or "The password does not meet the requirements.",
            instance=request.url.path,
        )

    @app.exception_handler(PasswordComplexityError)
    async def _pw_complexity(request: Request, exc: PasswordComplexityError):
        return _problem(
            400, code="AUTH_PASSWORD_INVALID",
            title="Password not accepted",
            detail=str(exc),
            instance=request.url.path,
        )

    @app.exception_handler(AuthError)
    async def _fallback(request: Request, exc: AuthError):
        return _problem(
            401, code="AUTH_ERROR",
            title="Authentication error",
            detail=str(exc) or "Authentication failed.",
            instance=request.url.path,
        )

    @app.exception_handler(HTTPException)
    async def _http(request: Request, exc: HTTPException):
        # Preserve non-auth HTTPExceptions (from the RBAC decorator, 404s, etc.)
        # but reshape into problem+json.
        code_map = {
            401: "AUTH_UNAUTHENTICATED",
            403: "AUTH_FORBIDDEN",
            404: "NOT_FOUND",
            409: "CONFLICT",
            429: "RATE_LIMITED",
        }
        code = code_map.get(exc.status_code, f"HTTP_{exc.status_code}")
        return _problem(
            exc.status_code, code=code,
            title=str(exc.detail) if isinstance(exc.detail, str) else "Error",
            detail=str(exc.detail) if isinstance(exc.detail, str) else "",
            instance=request.url.path,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        return _problem(
            422, code="VALIDATION_ERROR",
            title="Invalid request",
            detail="One or more fields failed validation.",
            instance=request.url.path,
            extra={"errors": exc.errors()},
        )
