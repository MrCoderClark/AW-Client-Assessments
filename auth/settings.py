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

    # ---- Entra SSO (docs/ENTRA_SSO.md) ----
    # sso_enabled is *derived*: the toggle must be on AND the three required
    # values present. A half-configured SSO never breaks password login boot.
    sso_enabled: bool = False
    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_client_secret: str = ""
    entra_redirect_uri: str = ""            # empty → derived from APP_BASE_URL at use site
    # Where Entra sends the browser after it clears its own session. Must be
    # registered as a post-logout redirect URI on the app registration.
    # Empty → derived as {APP_BASE_URL}/login at use site.
    entra_post_logout_redirect_uri: str = ""
    entra_scopes: str = "openid profile email"
    sso_state_ttl_seconds: int = 300


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

    # Entra SSO: derive `sso_enabled` from the toggle + presence of the three
    # required values. If the toggle is on but something's missing, warn and
    # stay disabled rather than crashing the app (password login must boot).
    entra_tenant = _opt("AUTH_ENTRA_TENANT_ID", "")
    entra_client = _opt("AUTH_ENTRA_CLIENT_ID", "")
    entra_secret = _opt("AUTH_ENTRA_CLIENT_SECRET", "")
    sso_toggle = _bool("AUTH_SSO_ENABLED", False)
    sso_ready = bool(entra_tenant and entra_client and entra_secret)
    sso_enabled = sso_toggle and sso_ready
    if sso_toggle and not sso_ready:
        import logging
        logging.getLogger("cfv.auth").warning(
            "AUTH_SSO_ENABLED is on but AUTH_ENTRA_TENANT_ID/CLIENT_ID/CLIENT_SECRET "
            "are not all set — SSO stays disabled; password login unaffected."
        )

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
        sso_enabled=sso_enabled,
        entra_tenant_id=entra_tenant,
        entra_client_id=entra_client,
        entra_client_secret=entra_secret,
        entra_redirect_uri=_opt("AUTH_ENTRA_REDIRECT_URI", ""),
        entra_post_logout_redirect_uri=_opt("AUTH_ENTRA_POST_LOGOUT_REDIRECT_URI", ""),
        entra_scopes=_opt("AUTH_ENTRA_SCOPES", "openid profile email"),
        sso_state_ttl_seconds=_int("AUTH_SSO_STATE_TTL", 300),
    )
