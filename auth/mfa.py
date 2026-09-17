"""MFA service — TOTP enrolment + verification, email-OTP fallback, backup codes.

Design (docs/PHASE15_AUTH.md M3, docs/ENTRA_SSO.md):
  - The *enrolled* factor is TOTP (authenticator app). Email OTP and backup
    codes are alternatives offered at challenge time — neither needs its own
    enrolment.
  - A login that needs MFA parks the user on a short-lived, HMAC-signed
    challenge (cookie `cfv_mfa`) instead of issuing a session. The /mfa/*
    endpoints verify a code against that challenge, then AuthService.issue_login
    mints the real session.
  - `required` = user not exempt AND (already enrolled OR global mfa_required).

Token issuance lives in AuthService; this service never mints app tokens.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from . import app_settings, totp
from .emails import MailSpec, _brand_name, send_mail
from .random import const_eq, new_id, numeric_code
from .settings import AuthSettings
from .sso import read_state_cookie, sign_state_cookie

CHALLENGE_TTL = 600        # seconds a login MFA challenge lives
EMAIL_CODE_TTL = 600       # seconds an emailed OTP lives
MAX_ATTEMPTS = 5           # wrong codes before the challenge is burned
BACKUP_CODE_COUNT = 10


@dataclass(frozen=True)
class MfaStatus:
    enrolled: bool
    exempt: bool


@dataclass(frozen=True)
class Challenge:
    id: str
    user_id: str
    purpose: str           # "verify" | "enroll"
    remember: bool
    attempts: int


class MfaError(Exception):
    """Base for MFA failures the route maps to a status code."""


class ChallengeInvalid(MfaError):
    """401 — challenge missing, expired, or burned."""


class CodeInvalid(MfaError):
    """400 — wrong/expired code (attempt counted)."""


class MfaService:
    def __init__(self, settings: AuthSettings) -> None:
        self.s = settings

    # ---- HMAC for indexable, high-entropy secrets (codes) ----
    def _hmac(self, value: str) -> str:
        return hmac.new(self.s.refresh_hash_secret, value.encode("utf-8"), hashlib.sha256).hexdigest()

    # ---- status / policy --------------------------------------------
    async def is_enrolled(self, conn: AsyncConnection, user_id: str) -> bool:
        r = await conn.execute(
            text("""SELECT 1 FROM mfa_factors
                    WHERE user_id = :u AND kind = 'TOTP' AND revoked_at IS NULL LIMIT 1"""),
            {"u": user_id},
        )
        return r.first() is not None

    async def status(self, conn: AsyncConnection, user_id: str) -> MfaStatus:
        r = await conn.execute(text("SELECT mfa_exempt FROM users WHERE id = :u"), {"u": user_id})
        row = r.first()
        exempt = bool(row.mfa_exempt) if row is not None else False
        return MfaStatus(enrolled=await self.is_enrolled(conn, user_id), exempt=exempt)

    async def required(self, conn: AsyncConnection, user_id: str) -> bool:
        st = await self.status(conn, user_id)
        if st.exempt:
            return False
        if st.enrolled:
            return True
        return await app_settings.get_bool(conn, "mfa_required")

    # ---- challenge lifecycle ----------------------------------------
    async def create_challenge(
        self, conn: AsyncConnection, user_id: str, *, purpose: str, remember: bool
    ) -> str:
        cid = new_id()
        await conn.execute(
            text("""
                INSERT INTO mfa_challenges
                    (id, user_id, session_seed, purpose, attempts, expires_at, created_at)
                VALUES (:id, :u, :seed, :p, 0, :exp, now())
            """),
            {"id": cid, "u": user_id, "seed": json.dumps({"remember": remember}),
             "p": purpose, "exp": datetime.now(UTC) + timedelta(seconds=CHALLENGE_TTL)},
        )
        return cid

    def sign_cookie(self, cid: str) -> str:
        return sign_state_cookie(
            {"cid": cid, "exp": int(datetime.now(UTC).timestamp()) + CHALLENGE_TTL},
            self.s.refresh_hash_secret,
        )

    def read_cookie(self, value: str) -> str:
        """Return the challenge id from a signed cookie, or raise ChallengeInvalid."""
        try:
            payload = read_state_cookie(value, self.s.refresh_hash_secret)
        except Exception as e:
            raise ChallengeInvalid(str(e)) from e
        cid = payload.get("cid")
        if not cid:
            raise ChallengeInvalid("no challenge id")
        return str(cid)

    async def load(self, conn: AsyncConnection, cid: str) -> Challenge:
        r = await conn.execute(
            text("""SELECT id, user_id, purpose, attempts, session_seed
                    FROM mfa_challenges WHERE id = :id AND expires_at > now()"""),
            {"id": cid},
        )
        row = r.first()
        if row is None:
            raise ChallengeInvalid("challenge expired or unknown")
        try:
            seed = json.loads(row.session_seed) if row.session_seed else {}
        except (ValueError, TypeError):
            seed = {}
        return Challenge(id=str(row.id), user_id=str(row.user_id),
                         purpose=str(row.purpose or "verify"),
                         remember=bool(seed.get("remember", False)),
                         attempts=int(row.attempts))

    async def delete_challenge(self, conn: AsyncConnection, cid: str) -> None:
        await conn.execute(text("DELETE FROM mfa_challenges WHERE id = :id"), {"id": cid})

    async def record_failure(self, conn: AsyncConnection, cid: str) -> None:
        """Count a wrong code; burn the challenge at the attempt ceiling.
        Always raises: ChallengeInvalid when burned, else CodeInvalid.

        Runs the increment/burn in its OWN committed transaction: this method
        always ends by raising, which rolls back the request's transaction — so
        the attempt counter would otherwise never persist (same reason the
        failed-login and rate-limit counters use separate transactions).
        """
        from .engine import transaction
        async with transaction() as own:
            r = await own.execute(
                text("UPDATE mfa_challenges SET attempts = attempts + 1 WHERE id = :id RETURNING attempts"),
                {"id": cid},
            )
            row = r.first()
            n = int(row.attempts) if row else MAX_ATTEMPTS
            if n >= MAX_ATTEMPTS:
                await own.execute(text("DELETE FROM mfa_challenges WHERE id = :id"), {"id": cid})
        if n >= MAX_ATTEMPTS:
            raise ChallengeInvalid("too many attempts")
        raise CodeInvalid("incorrect code")

    # ---- enrolment ---------------------------------------------------
    async def enroll_start(self, conn: AsyncConnection, cid: str, account_email: str) -> tuple[str, str]:
        """Generate a fresh TOTP secret, stash it on the challenge (pending),
        and return (secret, otpauth_uri) for the QR / manual entry."""
        secret = totp.random_secret()
        # session_seed is a TEXT column holding JSON — merge in Python.
        r = await conn.execute(text("SELECT session_seed FROM mfa_challenges WHERE id = :id"), {"id": cid})
        row = r.first()
        seed: dict[str, Any] = {}
        if row is not None and row.session_seed:
            try:
                seed = json.loads(row.session_seed)
            except (ValueError, TypeError):
                seed = {}
        seed["pending_secret"] = secret
        await conn.execute(
            text("UPDATE mfa_challenges SET session_seed = :seed WHERE id = :id"),
            {"seed": json.dumps(seed), "id": cid},
        )
        uri = totp.provisioning_uri(secret, account=account_email, issuer=_brand_name())
        return secret, uri

    async def pending_secret(self, conn: AsyncConnection, cid: str) -> str | None:
        r = await conn.execute(
            text("SELECT session_seed AS v FROM mfa_challenges WHERE id = :id"), {"id": cid})
        row = r.first()
        if row is None or not row.v:
            return None
        try:
            return json.loads(row.v).get("pending_secret")
        except (ValueError, TypeError):
            return None

    async def persist_totp(self, conn: AsyncConnection, user_id: str, secret: str) -> list[str]:
        """Activate a TOTP factor for the user (replacing any prior), and mint
        a fresh set of backup codes. Returns the plaintext codes (shown once)."""
        blob = totp.encrypt_secret(secret, self.s.refresh_hash_secret)
        await conn.execute(
            text("""UPDATE mfa_factors SET revoked_at = now()
                    WHERE user_id = :u AND kind = 'TOTP' AND revoked_at IS NULL"""),
            {"u": user_id},
        )
        await conn.execute(
            text("""INSERT INTO mfa_factors (id, user_id, kind, label, secret_encrypted, created_at)
                    VALUES (:id, :u, 'TOTP', 'Authenticator app', :b, now())"""),
            {"id": new_id(), "u": user_id, "b": blob},
        )
        await conn.execute(
            text("UPDATE users SET mfa_enrolled_at = COALESCE(mfa_enrolled_at, now()) WHERE id = :u"),
            {"u": user_id},
        )
        return await self._gen_backup_codes(conn, user_id)

    async def _gen_backup_codes(self, conn: AsyncConnection, user_id: str) -> list[str]:
        await conn.execute(text("DELETE FROM mfa_backup_codes WHERE user_id = :u"), {"u": user_id})
        codes: list[str] = []
        for _ in range(BACKUP_CODE_COUNT):
            raw = secrets.token_hex(4)  # 8 hex chars
            code = f"{raw[:4]}-{raw[4:]}"
            codes.append(code)
            await conn.execute(
                text("""INSERT INTO mfa_backup_codes (id, user_id, code_hash, created_at)
                        VALUES (:id, :u, :h, now())"""),
                {"id": new_id(), "u": user_id, "h": self._hmac(code)},
            )
        return codes

    # ---- verification ------------------------------------------------
    async def verify_totp(self, conn: AsyncConnection, user_id: str, code: str) -> bool:
        r = await conn.execute(
            text("""SELECT id, secret_encrypted FROM mfa_factors
                    WHERE user_id = :u AND kind = 'TOTP' AND revoked_at IS NULL
                    ORDER BY created_at DESC LIMIT 1"""),
            {"u": user_id},
        )
        row = r.first()
        if row is None or row.secret_encrypted is None:
            return False
        try:
            secret = totp.decrypt_secret(row.secret_encrypted, self.s.refresh_hash_secret)
        except Exception:
            return False
        if totp.verify(secret, code):
            await conn.execute(
                text("UPDATE mfa_factors SET last_used_at = now() WHERE id = :id"), {"id": row.id})
            return True
        return False

    async def verify_backup(self, conn: AsyncConnection, user_id: str, code: str) -> bool:
        h = self._hmac((code or "").strip().lower())
        r = await conn.execute(
            text("""SELECT id FROM mfa_backup_codes
                    WHERE user_id = :u AND code_hash = :h AND used_at IS NULL LIMIT 1"""),
            {"u": user_id, "h": h},
        )
        row = r.first()
        if row is None:
            return False
        await conn.execute(text("UPDATE mfa_backup_codes SET used_at = now() WHERE id = :id"), {"id": row.id})
        return True

    async def send_email_code(self, conn: AsyncConnection, cid: str, *, email: str, name: str | None) -> tuple[bool, str]:
        code = numeric_code(6)
        await conn.execute(
            text("""UPDATE mfa_challenges
                    SET email_code_hash = :h, email_code_expires_at = :exp WHERE id = :id"""),
            {"h": self._hmac(code), "exp": datetime.now(UTC) + timedelta(seconds=EMAIL_CODE_TTL), "id": cid},
        )
        greeting = name or "there"
        spec = MailSpec(
            subject=f"Your {_brand_name()} sign-in code",
            body=(f"Hi {greeting},\n\nYour sign-in verification code is: {code}\n\n"
                  f"It expires in 10 minutes. If you didn't try to sign in, ignore this email.\n"),
            preheader="Your one-time sign-in code",
        )
        # send_mail is blocking (Resend HTTP / SMTP) — offload off the event loop.
        return await anyio.to_thread.run_sync(send_mail, email, spec)

    async def verify_email_code(self, conn: AsyncConnection, cid: str, code: str) -> bool:
        r = await conn.execute(
            text("""SELECT email_code_hash AS h, email_code_expires_at AS exp
                    FROM mfa_challenges WHERE id = :id"""),
            {"id": cid},
        )
        row = r.first()
        if row is None or not row.h or row.exp is None or row.exp <= datetime.now(UTC):
            return False
        if const_eq(self._hmac((code or "").strip()), row.h):
            await conn.execute(
                text("""UPDATE mfa_challenges SET email_code_hash = NULL, email_code_expires_at = NULL
                        WHERE id = :id"""), {"id": cid})
            return True
        return False

    # ---- admin -------------------------------------------------------
    async def reset_user(self, conn: AsyncConnection, user_id: str) -> None:
        """Clear a user's 2FA entirely (support: lost device). They re-enrol on
        next login if MFA is still required."""
        await conn.execute(
            text("""UPDATE mfa_factors SET revoked_at = now()
                    WHERE user_id = :u AND revoked_at IS NULL"""), {"u": user_id})
        await conn.execute(text("DELETE FROM mfa_backup_codes WHERE user_id = :u"), {"u": user_id})
        await conn.execute(
            text("UPDATE users SET mfa_enrolled_at = NULL WHERE id = :u"), {"u": user_id})
