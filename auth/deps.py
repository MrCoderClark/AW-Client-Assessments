"""Process-wide service singletons + FastAPI dependencies.

Assembled once at first import; wired into api.py's startup so the auth
surface has one canonical instance of each service.
"""
from __future__ import annotations

from functools import lru_cache

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncConnection

from .engine import get_conn
from .passwords import PasswordService
from .service import AuthService
from .sessions import SessionService
from .settings import AuthSettings, load
from .tokens import TokenService


@lru_cache(maxsize=1)
def settings() -> AuthSettings:
    return load()


@lru_cache(maxsize=1)
def passwords() -> PasswordService:
    return PasswordService(settings())


@lru_cache(maxsize=1)
def tokens() -> TokenService:
    return TokenService(settings())


@lru_cache(maxsize=1)
def sessions_svc() -> SessionService:
    return SessionService(settings())


@lru_cache(maxsize=1)
def auth_service() -> AuthService:
    return AuthService(
        settings(),
        passwords=passwords(),
        tokens=tokens(),
        sessions=sessions_svc(),
    )


# FastAPI dependency-injection helpers (each just returns the singleton).
def dep_auth_service() -> AuthService:
    return auth_service()


def dep_tokens() -> TokenService:
    return tokens()


def dep_sessions() -> SessionService:
    return sessions_svc()


def dep_passwords() -> PasswordService:
    return passwords()


# Re-export the DB dependency for convenience.
DbConn = Depends(get_conn)
