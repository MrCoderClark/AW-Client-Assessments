"""JWT access tokens (Ed25519) + opaque refresh tokens.

Access token claims  (see docs/PHASE15_AUTH.md §B.5):
  iss, aud, sub (user_id), sid (session_id), jti, iat, exp, ver, role

verify_access() does pure-crypto/claim validation only. Runtime state
checks (jti revoked, session revoked, user.ver mismatch) happen in the
request middleware — separation of concerns, and keeps this class free
of DB dependencies.

Refresh tokens are 256-bit opaque URL-safe strings. They are stored as
HMAC-SHA256(server_secret, token) — deterministic (so we can index),
and a DB dump alone doesn't let anyone forge the hash without also
stealing AUTH_REFRESH_HASH_SECRET.

ponytail: Argon2 is the design's suggestion for the refresh hash but is
the wrong primitive for a high-entropy random token that we need to look
up in O(1). HMAC-SHA256 with a server secret is the correct fit.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any

import jwt as _jwt

from .random import new_id, opaque_token
from .settings import AuthSettings

ALGORITHM = "EdDSA"


class TokenError(Exception):
    """Any failure to verify a token — caller must not distinguish causes to the client."""


@dataclass(frozen=True)
class AccessClaims:
    sub: str      # user_id
    sid: str      # session_id
    jti: str
    role: str
    ver: int
    iat: int
    exp: int


class TokenService:
    def __init__(self, settings: AuthSettings) -> None:
        self.s = settings
        # PyJWT accepts PEM bytes directly for EdDSA.
        self._priv = settings.jwt_private_key_pem
        self._pub = settings.jwt_public_key_pem
        self._kid = settings.jwt_kid
        self._iss = settings.jwt_issuer
        self._aud = settings.jwt_audience
        self._ttl = settings.access_token_ttl_seconds

    # ---- access token ------------------------------------------------

    def issue_access(
        self,
        *,
        user_id: str,
        session_id: str,
        role: str,
        ver: int,
    ) -> tuple[str, int]:
        """Return (jwt, expires_in_seconds)."""
        now = int(time.time())
        claims = {
            "iss": self._iss,
            "aud": self._aud,
            "sub": user_id,
            "sid": session_id,
            "jti": new_id(),
            "iat": now,
            "exp": now + self._ttl,
            "ver": ver,
            "role": role,
        }
        token = _jwt.encode(
            claims,
            self._priv,
            algorithm=ALGORITHM,
            headers={"kid": self._kid},
        )
        return token, self._ttl

    def verify_access(self, token: str) -> AccessClaims:
        """Return claims on success; raise TokenError on any failure.

        Checks: signature, `iss`, `aud`, `exp`, `iat` present. Does NOT
        check jti/session/ver against the DB — middleware does that.
        """
        try:
            raw: dict[str, Any] = _jwt.decode(
                token,
                self._pub,
                algorithms=[ALGORITHM],
                issuer=self._iss,
                audience=self._aud,
                options={"require": ["exp", "iat", "sub", "sid", "jti", "ver", "role"]},
            )
        except _jwt.PyJWTError as e:
            raise TokenError(str(e)) from e
        return AccessClaims(
            sub=str(raw["sub"]),
            sid=str(raw["sid"]),
            jti=str(raw["jti"]),
            role=str(raw["role"]),
            ver=int(raw["ver"]),
            iat=int(raw["iat"]),
            exp=int(raw["exp"]),
        )

    # ---- refresh token -----------------------------------------------

    def new_refresh_token(self) -> str:
        """256 bits of random, URL-safe base64."""
        return opaque_token(32)

    def hash_refresh(self, token: str) -> str:
        """HMAC-SHA256 hex. Deterministic → indexable in the sessions table."""
        return hmac.new(
            self.s.refresh_hash_secret,
            token.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()

    # ---- JWKS-ish public export --------------------------------------

    def public_key_info(self) -> dict[str, str]:
        """What the /auth/jwks endpoint returns for this signing key.

        Not a full JWKS document — that's built by the JWKS route from a
        list of these. M1 has one key; multi-key rotation lands with M6.
        """
        return {"kid": self._kid, "alg": ALGORITHM, "pem": self._pub.decode("ascii")}
