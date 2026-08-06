"""Backwards-compat re-export shim. Prefer importing directly from the
focused modules (auth.passwords, auth.tokens, auth.sessions, auth.service).
"""
from .passwords import PasswordService
from .service import AuthService, MePayload, TokenPair
from .sessions import SessionService
from .tokens import TokenService

__all__ = [
    "PasswordService", "TokenService", "SessionService", "AuthService",
    "TokenPair", "MePayload",
]
