"""AuthService — orchestrates login, refresh, logout, me.

Public methods raise `AuthError` (subclasses) on failure; the route
translates each to an HTTP status via the RFC 9457 handler in errors.py.

M1 scope:
  - login (with constant-time timing on user-not-found)
  - refresh (with refresh-family reuse detection → nuclear per-user revoke)
  - logout
  - get_me (user profile + permission set)

Deferred to next mini-branch: verify_email, accept_invite, change_password.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .audit import AuditEvent, audit_logger
from .context import ANONYMOUS, AuthContext
from .passwords import PasswordService
from .permissions import resolve_for_role
from .sessions import SessionService
from .settings import AuthSettings
from .tokens import TokenService


def _actor(actor_kind: str = "anonymous", user_id: str | None = None, request_id: str = "") -> AuthContext:
    return AuthContext(actor_kind=actor_kind, user_id=user_id, request_id=request_id)


# ---------- errors ---------------------------------------------------

class AuthError(Exception):
    """Base."""


class InvalidCredentials(AuthError):
    """401 — user missing, wrong password, or user not in a login-able state."""


class AccountLocked(AuthError):
    """423 — user has locked_until in the future."""


class RefreshInvalid(AuthError):
    """401 — refresh token unknown, expired, or has been reused."""


class SessionRevoked(AuthError):
    """401 — access token points at a revoked session or stale user.ver."""


class InvalidToken(AuthError):
    """400/401 — an invite / verify / reset token is unknown, used, or expired."""


class PasswordInvalid(AuthError):
    """400 — new password fails complexity check."""


# ---------- value objects --------------------------------------------

@dataclass(frozen=True)
class TokenPair:
    access_token: str
    access_expires_in: int
    refresh_token: str
    session_id: str


@dataclass(frozen=True)
class ProfileSummary:
    id: str
    name: str
    layout_key: str


@dataclass(frozen=True)
class MePayload:
    user_id: str
    email: str
    first_name: str | None
    last_name: str | None
    display_name: str | None
    role: str
    status: str
    permissions: list[str]
    must_change_password: bool
    profile: ProfileSummary | None
    # Per-user widget override. None = use the widget list defined by the
    # profile's layout_key. List = custom composition (widget-registry keys,
    # in render order).
    dashboard_widgets: list[str] | None


# ---------- service --------------------------------------------------

class AuthService:
    def __init__(
        self,
        settings: AuthSettings,
        *,
        passwords: PasswordService,
        tokens: TokenService,
        sessions: SessionService,
    ) -> None:
        self.s = settings
        self.passwords = passwords
        self.tokens = tokens
        self.sessions = sessions

    # ---- login ------------------------------------------------------

    async def login(
        self,
        conn: AsyncConnection,
        *,
        email: str,
        password: str,
        remember_me: bool,
        ip_address: str,
        user_agent: str,
        request_id: str = "",
    ) -> TokenPair:
        norm = email.strip().lower()
        r = await conn.execute(
            text("""
                SELECT id, email, password_hash, ver, role, status, locked_until,
                       must_change_password
                FROM users WHERE email_normalized = :e
            """),
            {"e": norm},
        )
        user = r.first()

        anon = _actor(request_id=request_id)
        if user is None or user.password_hash is None:
            # constant-time dummy verify so we don't leak user existence via timing
            self.passwords.verify_against_dummy(password)
            await audit_logger().emit(
                None, anon,
                AuditEvent(action="AUTH_LOGIN_FAILURE", outcome="failure",
                           context={"email": norm, "reason": "no_user"}),
                actor_ip=ip_address, user_agent=user_agent,
            )
            raise InvalidCredentials("invalid credentials")

        if user.locked_until is not None and user.locked_until > datetime.now(UTC):
            await audit_logger().emit(
                None, anon,
                AuditEvent(action="AUTH_LOGIN_FAILURE", outcome="denied", severity="SEC",
                           target_type="user", target_id=str(user.id),
                           context={"reason": "locked", "locked_until": user.locked_until.isoformat()}),
                actor_ip=ip_address, user_agent=user_agent,
            )
            raise AccountLocked("account temporarily locked")

        if user.status not in ("ACTIVE",):
            # constant-time verify still — same reason
            self.passwords.verify_against_dummy(password)
            await audit_logger().emit(
                None, anon,
                AuditEvent(action="AUTH_LOGIN_FAILURE", outcome="denied",
                           target_type="user", target_id=str(user.id),
                           context={"reason": "not_active", "status": str(user.status)}),
                actor_ip=ip_address, user_agent=user_agent,
            )
            raise InvalidCredentials("invalid credentials")

        if not self.passwords.verify(password, user.password_hash):
            await audit_logger().emit(
                None, anon,
                AuditEvent(action="AUTH_LOGIN_FAILURE", outcome="failure",
                           target_type="user", target_id=str(user.id),
                           context={"reason": "bad_password"}),
                actor_ip=ip_address, user_agent=user_agent,
            )
            locked = await _commit_failed_attempt(
                user_id=str(user.id),
                threshold=self.s.lockout_threshold,
                window_minutes=self.s.lockout_window_minutes,
                ip_address=ip_address, user_agent=user_agent,
                request_id=request_id,
            )
            if locked:
                raise AccountLocked("account temporarily locked")
            raise InvalidCredentials("invalid credentials")

        # Success → possibly rehash if params moved
        if self.passwords.needs_rehash(user.password_hash):
            await conn.execute(
                text("UPDATE users SET password_hash = :h WHERE id = :id"),
                {"h": self.passwords.hash(password), "id": user.id},
            )

        # Reset failed attempts, update last_login_*
        await conn.execute(
            text("""
                UPDATE users
                SET failed_login_attempts = 0,
                    last_login_at = now(),
                    last_login_ip = :ip
                WHERE id = :id
            """),
            {"ip": ip_address, "id": user.id},
        )

        pair = await self._issue_pair(
            conn,
            user_id=str(user.id),
            role=str(user.role),
            ver=int(user.ver),
            user_agent=user_agent,
            ip_address=ip_address,
            remember_me=remember_me,
            parent_id=None,
        )
        await audit_logger().emit(
            None,
            _actor(actor_kind="user", user_id=str(user.id), request_id=request_id),
            AuditEvent(action="AUTH_LOGIN_SUCCESS",
                       target_type="user", target_id=str(user.id),
                       context={"session_id": pair.session_id, "remember_me": remember_me}),
            actor_ip=ip_address, user_agent=user_agent,
        )
        return pair

    # ---- refresh -----------------------------------------------------

    async def refresh(
        self,
        conn: AsyncConnection,
        *,
        refresh_token: str,
        ip_address: str,
        user_agent: str,
        request_id: str = "",
    ) -> TokenPair:
        h = self.tokens.hash_refresh(refresh_token)
        row = await self.sessions.find_by_refresh_hash(conn, h)
        anon = _actor(request_id=request_id)
        if row is None:
            await audit_logger().emit(
                None, anon,
                AuditEvent(action="AUTH_TOKEN_REFRESH_FAILURE", outcome="failure",
                           context={"reason": "unknown_token"}),
                actor_ip=ip_address, user_agent=user_agent,
            )
            raise RefreshInvalid("refresh token not recognized")

        # Reuse detection: a session that has already been rotated (revoked)
        # is presenting its old refresh token. Nuclear-revoke all this user's
        # sessions and audit a security event.
        #
        # ponytail: this work MUST persist even though we're about to raise
        # RefreshInvalid — the raise rolls back the caller's transaction.
        # Use a separate committed transaction for the security side effects.
        if row.revoked_at is not None:
            await _commit_reuse_security(
                user_id=row.user_id,
                session_id=row.id,
                ip_address=ip_address,
                request_id=request_id,
                user_agent=user_agent,
            )
            raise RefreshInvalid("refresh token reuse detected")

        if row.expires_at <= datetime.now(UTC):
            await self.sessions.revoke(conn, row.id, reason="expired")
            await audit_logger().emit(
                None,
                _actor(actor_kind="user", user_id=row.user_id, request_id=request_id),
                AuditEvent(action="AUTH_TOKEN_REFRESH_FAILURE", outcome="failure",
                           target_type="session", target_id=row.id,
                           context={"reason": "expired"}),
                actor_ip=ip_address, user_agent=user_agent,
            )
            raise RefreshInvalid("refresh token expired")

        # Fetch user's current ver + role
        u = await conn.execute(
            text("SELECT ver, role, status FROM users WHERE id = :id"),
            {"id": row.user_id},
        )
        urow = u.first()
        if urow is None or urow.status != "ACTIVE":
            await self.sessions.revoke(conn, row.id, reason="user_inactive")
            raise RefreshInvalid("user inactive")

        new_refresh = self.tokens.new_refresh_token()
        new_hash = self.tokens.hash_refresh(new_refresh)
        new_sid = await self.sessions.rotate(
            conn,
            row.id,
            user_id=row.user_id,
            new_refresh_hash=new_hash,
            user_agent=user_agent,
            ip_address=ip_address,
            remember_me=row.remember_me,
        )
        access, ttl = self.tokens.issue_access(
            user_id=row.user_id, session_id=new_sid,
            role=str(urow.role), ver=int(urow.ver),
        )
        pair = TokenPair(
            access_token=access, access_expires_in=ttl,
            refresh_token=new_refresh, session_id=new_sid,
        )
        await audit_logger().emit(
            None,
            _actor(actor_kind="user", user_id=row.user_id, request_id=request_id),
            AuditEvent(action="AUTH_TOKEN_REFRESH",
                       target_type="session", target_id=new_sid,
                       context={"parent_session_id": row.id}),
            actor_ip=ip_address, user_agent=user_agent,
        )
        return pair

    # ---- logout -----------------------------------------------------

    async def logout(
        self, conn: AsyncConnection, *, session_id: str,
        user_id: str | None = None, ip_address: str | None = None,
        user_agent: str | None = None, request_id: str = "",
    ) -> None:
        await self.sessions.revoke(conn, session_id, reason="logout")
        await audit_logger().emit(
            None,
            _actor(actor_kind="user", user_id=user_id, request_id=request_id),
            AuditEvent(action="AUTH_LOGOUT", target_type="session", target_id=session_id),
            actor_ip=ip_address, user_agent=user_agent,
        )

    async def logout_all(
        self, conn: AsyncConnection, *, user_id: str,
        ip_address: str | None = None, user_agent: str | None = None,
        request_id: str = "",
    ) -> int:
        n = await self.sessions.revoke_all_for_user(conn, user_id, reason="logout_all")
        await audit_logger().emit(
            None,
            _actor(actor_kind="user", user_id=user_id, request_id=request_id),
            AuditEvent(action="AUTH_LOGOUT_ALL",
                       target_type="user", target_id=user_id,
                       context={"sessions_revoked": n}),
            actor_ip=ip_address, user_agent=user_agent,
        )
        return n

    # ---- invitation acceptance ---------------------------------------

    async def accept_invite(
        self,
        conn: AsyncConnection,
        *,
        token: str,
        password: str,
        first_name: str,
        last_name: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        request_id: str = "",
    ) -> str:
        """Consume an invitation token, activate the user, set their password.
        Returns user_id.
        """
        from .random import const_eq
        # Complexity is checked first — cheap fail before token I/O.
        self.passwords.check_complexity(password)

        row = await conn.execute(
            text("""
                SELECT i.id, i.email, i.role, i.expires_at, i.accepted_at, i.revoked_at,
                       i.token_hash, u.id AS user_id, u.first_name, u.last_name
                FROM invitations i
                LEFT JOIN users u ON u.email_normalized = LOWER(i.email)
                WHERE i.token_hash = :h
            """),
            {"h": self._hash_token(token)},
        )
        inv = row.first()
        if inv is None or inv.accepted_at is not None or inv.revoked_at is not None:
            raise InvalidToken("invitation token not valid")
        if inv.expires_at <= datetime.now(UTC):
            raise InvalidToken("invitation has expired")
        if inv.user_id is None:
            raise InvalidToken("invitation is not attached to a user")

        first = (first_name or "").strip()
        last = (last_name or "").strip()
        hashed = self.passwords.hash(password)

        # If the invitee left name fields blank, keep whatever the admin
        # entered when they created the invite. Only overwrite when non-empty.
        await conn.execute(
            text("""
                UPDATE users
                SET password_hash = :ph,
                    password_updated_at = now(),
                    first_name    = COALESCE(NULLIF(:fn, ''), first_name),
                    last_name     = COALESCE(NULLIF(:ln, ''), last_name),
                    display_name  = COALESCE(
                                      NULLIF(TRIM(CONCAT_WS(' ',
                                        COALESCE(NULLIF(:fn, ''), first_name),
                                        COALESCE(NULLIF(:ln, ''), last_name))), ''),
                                      display_name),
                    status = 'ACTIVE',
                    email_verified_at = COALESCE(email_verified_at, now()),
                    must_change_password = false,
                    ver = ver + 1,
                    updated_at = now()
                WHERE id = :uid
            """),
            {"ph": hashed, "fn": first, "ln": last, "uid": inv.user_id},
        )
        await conn.execute(
            text("UPDATE invitations SET accepted_at = now() WHERE id = :id"),
            {"id": inv.id},
        )
        await audit_logger().emit(
            None,
            _actor(actor_kind="user", user_id=str(inv.user_id), request_id=request_id),
            AuditEvent(action="USER_ACCEPTED_INVITE",
                       target_type="user", target_id=str(inv.user_id),
                       context={"invitation_id": str(inv.id), "email": str(inv.email)}),
            actor_ip=ip_address, user_agent=user_agent,
        )
        return str(inv.user_id)

    # ---- email verification ------------------------------------------

    async def verify_email(
        self, conn: AsyncConnection, *, token: str,
        ip_address: str | None = None, user_agent: str | None = None,
        request_id: str = "",
    ) -> str:
        row = await conn.execute(
            text("""
                SELECT id, user_id, expires_at, used_at
                FROM email_verifications WHERE token_hash = :h
            """),
            {"h": self._hash_token(token)},
        )
        v = row.first()
        if v is None or v.used_at is not None:
            raise InvalidToken("verification token not valid")
        if v.expires_at <= datetime.now(UTC):
            raise InvalidToken("verification link has expired")
        await conn.execute(
            text("UPDATE email_verifications SET used_at = now() WHERE id = :id"),
            {"id": v.id},
        )
        await conn.execute(
            text("UPDATE users SET email_verified_at = COALESCE(email_verified_at, now()) WHERE id = :uid"),
            {"uid": v.user_id},
        )
        await audit_logger().emit(
            None,
            _actor(actor_kind="user", user_id=str(v.user_id), request_id=request_id),
            AuditEvent(action="AUTH_EMAIL_VERIFY_SUCCESS",
                       target_type="user", target_id=str(v.user_id)),
            actor_ip=ip_address, user_agent=user_agent,
        )
        return str(v.user_id)

    # ---- password reset ----------------------------------------------

    async def request_password_reset(
        self,
        conn: AsyncConnection,
        *,
        email: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        request_id: str = "",
    ) -> tuple[str, str, str] | None:
        """Return (plaintext token, user email, display name) so the caller
        can send the reset email. None if no such user — caller responds 202
        either way to avoid leaking user existence.
        """
        row = await conn.execute(
            text("SELECT id, email, display_name FROM users WHERE email_normalized = :e AND deleted_at IS NULL"),
            {"e": email.strip().lower()},
        )
        u = row.first()
        if u is None:
            return None
        from .random import opaque_token
        token = opaque_token(32)
        await conn.execute(
            text("""
                INSERT INTO password_resets (id, user_id, token_hash, expires_at)
                VALUES (:id, :uid, :h, now() + interval '30 minutes')
            """),
            {"id": _new_id(), "uid": u.id, "h": self._hash_token(token)},
        )
        await audit_logger().emit(
            None,
            _actor(request_id=request_id),
            AuditEvent(action="AUTH_PASSWORD_RESET_REQUESTED",
                       target_type="user", target_id=str(u.id),
                       context={"email": str(u.email)}),
            actor_ip=ip_address, user_agent=user_agent,
        )
        return token, str(u.email), (u.display_name or "")

    async def complete_password_reset(
        self,
        conn: AsyncConnection,
        *,
        token: str,
        new_password: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        request_id: str = "",
    ) -> tuple[str, str, str]:
        """Return (user_id, email, display_name) so the caller can send the
        password-changed alert."""
        self.passwords.check_complexity(new_password)
        row = await conn.execute(
            text("""
                SELECT r.id, r.user_id, r.expires_at, r.used_at,
                       u.email, u.display_name
                FROM password_resets r JOIN users u ON u.id = r.user_id
                WHERE r.token_hash = :h
            """),
            {"h": self._hash_token(token)},
        )
        r = row.first()
        if r is None or r.used_at is not None:
            raise InvalidToken("reset token not valid")
        if r.expires_at <= datetime.now(UTC):
            raise InvalidToken("reset link has expired")

        hashed = self.passwords.hash(new_password)
        await conn.execute(
            text("""
                UPDATE users
                SET password_hash = :h, password_updated_at = now(),
                    must_change_password = false,
                    ver = ver + 1, updated_at = now()
                WHERE id = :uid
            """),
            {"h": hashed, "uid": r.user_id},
        )
        await conn.execute(
            text("UPDATE password_resets SET used_at = now() WHERE id = :id"),
            {"id": r.id},
        )
        await self.sessions.revoke_all_for_user(conn, str(r.user_id), reason="password_reset")
        await audit_logger().emit(
            None,
            _actor(actor_kind="user", user_id=str(r.user_id), request_id=request_id),
            AuditEvent(action="AUTH_PASSWORD_RESET", severity="SEC",
                       target_type="user", target_id=str(r.user_id)),
            actor_ip=ip_address, user_agent=user_agent,
        )
        return str(r.user_id), str(r.email), (r.display_name or "")

    # ---- password change (self-service, requires current password) ---

    async def change_password(
        self,
        conn: AsyncConnection,
        *,
        user_id: str,
        current: str,
        new: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        request_id: str = "",
    ) -> tuple[str, str]:
        """Return (email, display_name) so the caller can send the alert."""
        self.passwords.check_complexity(new)
        row = await conn.execute(
            text("SELECT email, display_name, password_hash FROM users WHERE id = :id"),
            {"id": user_id},
        )
        u = row.first()
        if u is None or u.password_hash is None:
            raise InvalidCredentials("invalid credentials")
        if not self.passwords.verify(current, u.password_hash):
            await audit_logger().emit(
                None,
                _actor(actor_kind="user", user_id=user_id, request_id=request_id),
                AuditEvent(action="AUTH_PASSWORD_CHANGE_FAILURE", outcome="failure",
                           target_type="user", target_id=user_id,
                           context={"reason": "bad_current"}),
                actor_ip=ip_address, user_agent=user_agent,
            )
            raise InvalidCredentials("current password is incorrect")
        hashed = self.passwords.hash(new)
        await conn.execute(
            text("""
                UPDATE users
                SET password_hash = :h, password_updated_at = now(),
                    must_change_password = false,
                    ver = ver + 1, updated_at = now()
                WHERE id = :uid
            """),
            {"h": hashed, "uid": user_id},
        )
        await self.sessions.revoke_all_for_user(conn, user_id, reason="password_change")
        await audit_logger().emit(
            None,
            _actor(actor_kind="user", user_id=user_id, request_id=request_id),
            AuditEvent(action="AUTH_PASSWORD_CHANGED", severity="SEC",
                       target_type="user", target_id=user_id),
            actor_ip=ip_address, user_agent=user_agent,
        )
        return str(u.email), (u.display_name or "")

    # ---- me ---------------------------------------------------------

    async def get_me(self, conn: AsyncConnection, *, user_id: str) -> MePayload:
        r = await conn.execute(
            text("""
                SELECT u.id, u.email, u.first_name, u.last_name, u.display_name,
                       u.role, u.status, u.must_change_password,
                       u.dashboard_widgets,
                       p.id AS profile_id, p.name AS profile_name,
                       p.layout_key AS profile_layout_key
                FROM users u
                LEFT JOIN profiles p ON p.id = u.profile_id
                WHERE u.id = :id
            """),
            {"id": user_id},
        )
        u = r.first()
        if u is None:
            raise InvalidCredentials("user not found")
        profile = None
        if u.profile_id is not None:
            profile = ProfileSummary(
                id=str(u.profile_id),
                name=str(u.profile_name),
                layout_key=str(u.profile_layout_key),
            )
        widgets = u.dashboard_widgets
        if widgets is not None and not isinstance(widgets, list):
            widgets = None
        return MePayload(
            user_id=str(u.id),
            email=str(u.email),
            first_name=u.first_name,
            last_name=u.last_name,
            display_name=u.display_name,
            role=str(u.role),
            status=str(u.status),
            permissions=sorted(resolve_for_role(str(u.role))),
            must_change_password=bool(u.must_change_password),
            profile=profile,
            dashboard_widgets=widgets,
        )

    async def issue_login(
        self,
        conn: AsyncConnection,
        *,
        user_id: str,
        role: str,
        ver: int,
        ip_address: str,
        user_agent: str,
        remember_me: bool = False,
    ) -> TokenPair:
        """Public wrapper around _issue_pair. Used by flows that want to
        log a user in without going through the /login endpoint (accept
        invite, complete SSO in the future)."""
        return await self._issue_pair(
            conn,
            user_id=user_id, role=role, ver=ver,
            user_agent=user_agent, ip_address=ip_address,
            remember_me=remember_me, parent_id=None,
        )

    # ---- internals --------------------------------------------------

    def _hash_token(self, token: str) -> str:
        """HMAC-SHA256 of an opaque single-use token, for indexable storage.
        Reuses the refresh-token secret — one keying material for all opaque
        tokens is fine; they never appear together in a compromise scenario.
        """
        return self.tokens.hash_refresh(token)

    async def _issue_pair(
        self,
        conn: AsyncConnection,
        *,
        user_id: str,
        role: str,
        ver: int,
        user_agent: str,
        ip_address: str,
        remember_me: bool,
        parent_id: str | None,
    ) -> TokenPair:
        refresh = self.tokens.new_refresh_token()
        rh = self.tokens.hash_refresh(refresh)
        sid = await self.sessions.create(
            conn,
            user_id=user_id,
            refresh_hash=rh,
            user_agent=user_agent,
            ip_address=ip_address,
            remember_me=remember_me,
            parent_id=parent_id,
        )
        access, ttl = self.tokens.issue_access(
            user_id=user_id, session_id=sid, role=role, ver=ver
        )
        return TokenPair(
            access_token=access, access_expires_in=ttl,
            refresh_token=refresh, session_id=sid,
        )


def _new_id() -> str:
    from .random import new_id
    return new_id()


async def _commit_reuse_security(
    *, user_id: str, session_id: str, ip_address: str,
    request_id: str = "", user_agent: str | None = None,
) -> None:
    """Persist refresh-reuse side effects in a new committed transaction.

    Isolated from the caller's tx so the outer `raise RefreshInvalid`
    doesn't roll these writes back.
    """
    from .engine import transaction
    async with transaction() as sec_conn:
        await sec_conn.execute(
            text("""
                UPDATE sessions
                SET revoked_at = now(), revoked_reason = 'refresh_reuse'
                WHERE user_id = :uid AND revoked_at IS NULL
            """),
            {"uid": user_id},
        )
        await sec_conn.execute(
            text("""
                INSERT INTO security_events (id, kind, user_id, ip_address, context_json)
                VALUES (:id, 'REFRESH_REUSE', :uid, :ip, CAST(:ctx AS jsonb))
            """),
            {
                "id": _new_id(), "uid": user_id, "ip": ip_address,
                "ctx": '{"session_id":"' + session_id + '"}',
            },
        )
        await audit_logger().emit(
            sec_conn,
            _actor(actor_kind="system", user_id=user_id, request_id=request_id),
            AuditEvent(action="AUTH_REFRESH_REUSE_DETECTED", outcome="denied", severity="SEC",
                       target_type="session", target_id=session_id,
                       context={"user_id": user_id}),
            actor_ip=ip_address, user_agent=user_agent,
        )
    from .notifications import NotificationEvent, emit_for_category
    await emit_for_category(NotificationEvent(
        category="security", kind="refresh_reuse", severity="SEC",
        title="Refresh-token reuse detected",
        body="An old refresh token was replayed. All sessions for that user have been revoked.",
        url="/admin/users",
        context={"user_id": user_id, "session_id": session_id, "ip": ip_address},
    ))


async def _commit_failed_attempt(
    *, user_id: str, threshold: int, window_minutes: int,
    ip_address: str | None, user_agent: str | None, request_id: str = "",
) -> bool:
    """Bump failed_login_attempts in a fresh committed tx; lock if >= threshold.
    Returns True when the lockout was applied so the caller can raise
    AccountLocked instead of InvalidCredentials.
    """
    from .engine import transaction
    async with transaction() as sec_conn:
        r = await sec_conn.execute(
            text("""
                UPDATE users
                SET failed_login_attempts = failed_login_attempts + 1
                WHERE id = :id
                RETURNING failed_login_attempts
            """),
            {"id": user_id},
        )
        row = r.first()
        attempts = int(row[0]) if row else 0
        if attempts < threshold:
            return False
        await sec_conn.execute(
            text(f"""
                UPDATE users
                SET locked_until = now() + interval '{int(window_minutes)} minutes',
                    failed_login_attempts = 0
                WHERE id = :id
            """),
            {"id": user_id},
        )
        await audit_logger().emit(
            sec_conn,
            _actor(actor_kind="system", user_id=user_id, request_id=request_id),
            AuditEvent(action="SEC_LOCKOUT_STARTED", outcome="denied", severity="SEC",
                       target_type="user", target_id=user_id,
                       context={"attempts": attempts, "window_minutes": window_minutes}),
            actor_ip=ip_address, user_agent=user_agent,
        )
    # Notify admins/operators outside the sec_conn tx (fanout uses its own tx).
    from .notifications import NotificationEvent, emit_for_category
    await emit_for_category(NotificationEvent(
        category="security", kind="lockout_started", severity="SEC",
        title="Account locked",
        body=f"An account was locked after {attempts} failed attempts.",
        url="/admin/users",
        context={"user_id": user_id, "window_minutes": window_minutes},
    ))
    return True
