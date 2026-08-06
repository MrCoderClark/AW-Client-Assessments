"""Config pulled from env at import time. Fail loudly if required vars are missing.

ponytail: no config framework, no BaseSettings — os.environ + a couple of
type-cast helpers is enough.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _req(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"{name} is not set")
    return v


def _opt(name: str, default: str) -> str:
    return os.environ.get(name) or default


def _int(name: str, default: int) -> int:
    v = os.environ.get(name)
    return int(v) if v else default


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AuthSettings:
    database_url: str

    access_token_ttl_seconds: int
    refresh_token_ttl_seconds: int
    refresh_remember_me_ttl_seconds: int

    jwt_issuer: str
    jwt_audience: str
    jwt_kid: str
    jwt_private_key_pem: bytes
    jwt_public_key_pem: bytes

    refresh_hash_secret: bytes

    argon2_time_cost: int
    argon2_memory_kib: int
    argon2_parallelism: int

    hibp_enabled: bool
    hibp_timeout_seconds: float

    lockout_threshold: int
    lockout_window_minutes: int
    rl_login_ip_limit: int
    rl_login_ip_window_seconds: int
    rl_login_email_limit: int
    rl_login_email_window_seconds: int
    rl_forgot_email_limit: int
    rl_forgot_email_window_seconds: int
    rl_forgot_ip_limit: int
    rl_forgot_ip_window_seconds: int


def load() -> AuthSettings:
    from pathlib import Path
    priv = Path(_req("AUTH_JWT_PRIVATE_KEY_PATH")).read_bytes()
    pub = Path(_req("AUTH_JWT_PUBLIC_KEY_PATH")).read_bytes()
    refresh_secret_hex = _req("AUTH_REFRESH_HASH_SECRET")
    try:
        refresh_secret = bytes.fromhex(refresh_secret_hex)
    except ValueError as e:
        raise RuntimeError("AUTH_REFRESH_HASH_SECRET must be hex") from e
    if len(refresh_secret) < 32:
        raise RuntimeError("AUTH_REFRESH_HASH_SECRET must decode to >= 32 bytes")
    return AuthSettings(
        database_url=_req("DATABASE_URL"),
        access_token_ttl_seconds=_int("AUTH_ACCESS_TTL", 15 * 60),
        refresh_token_ttl_seconds=_int("AUTH_REFRESH_TTL", 30 * 24 * 3600),
        refresh_remember_me_ttl_seconds=_int("AUTH_REFRESH_REMEMBER_TTL", 90 * 24 * 3600),
        jwt_issuer=_opt("AUTH_JWT_ISSUER", "cfv"),
        jwt_audience=_opt("AUTH_JWT_AUDIENCE", "cfv-app"),
        jwt_kid=_req("AUTH_JWT_KID"),
        jwt_private_key_pem=priv,
        jwt_public_key_pem=pub,
        refresh_hash_secret=refresh_secret,
        argon2_time_cost=_int("AUTH_ARGON2_TIME", 3),
        argon2_memory_kib=_int("AUTH_ARGON2_MEMORY_KIB", 64 * 1024),
        argon2_parallelism=_int("AUTH_ARGON2_PARALLELISM", 4),
        hibp_enabled=_bool("AUTH_HIBP_ENABLED", True),
        hibp_timeout_seconds=float(_opt("AUTH_HIBP_TIMEOUT", "3.0")),
        lockout_threshold=_int("AUTH_LOCKOUT_THRESHOLD", 5),
        lockout_window_minutes=_int("AUTH_LOCKOUT_WINDOW_MIN", 15),
        rl_login_ip_limit=_int("AUTH_RL_LOGIN_IP_LIMIT", 5),
        rl_login_ip_window_seconds=_int("AUTH_RL_LOGIN_IP_WINDOW", 15 * 60),
        rl_login_email_limit=_int("AUTH_RL_LOGIN_EMAIL_LIMIT", 10),
        rl_login_email_window_seconds=_int("AUTH_RL_LOGIN_EMAIL_WINDOW", 15 * 60),
        rl_forgot_email_limit=_int("AUTH_RL_FORGOT_EMAIL_LIMIT", 3),
        rl_forgot_email_window_seconds=_int("AUTH_RL_FORGOT_EMAIL_WINDOW", 3600),
        rl_forgot_ip_limit=_int("AUTH_RL_FORGOT_IP_LIMIT", 20),
        rl_forgot_ip_window_seconds=_int("AUTH_RL_FORGOT_IP_WINDOW", 3600),
    )
