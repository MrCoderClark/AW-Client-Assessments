"""initial auth schema

Revision ID: 89986958c76a
Revises:
Create Date: 2026-08-03 10:16:09.672142

Fresh Phase 15 auth schema per docs/PHASE15_AUTH.md §B.0.
Covers M1–M5 tables. M6 (custom roles / permissions / teams / feature flags)
is deferred.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "89986958c76a"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all Phase 15 auth tables."""

    # UUIDs come from Python (uuidv7); no server-side default needed.
    UUID = postgresql.UUID(as_uuid=False)
    TS = sa.TIMESTAMP(timezone=True)

    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------
    op.execute(
        "CREATE TYPE user_role AS ENUM ('admin', 'operator', 'viewer')"
    )
    op.execute(
        "CREATE TYPE user_status AS ENUM "
        "('INVITED', 'ACTIVE', 'SUSPENDED', 'DEACTIVATED', 'SOFT_DELETED')"
    )
    op.create_table(
        "users",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("email_normalized", sa.Text, nullable=False, unique=True),
        sa.Column("email_verified_at", TS, nullable=True),
        sa.Column("display_name", sa.Text, nullable=True),
        sa.Column("first_name", sa.Text, nullable=True),
        sa.Column("last_name", sa.Text, nullable=True),
        sa.Column("avatar_url", sa.Text, nullable=True),
        sa.Column("time_zone", sa.Text, nullable=False, server_default="America/New_York"),
        sa.Column("locale", sa.Text, nullable=False, server_default="en-US"),
        sa.Column(
            "role",
            postgresql.ENUM("admin", "operator", "viewer", name="user_role", create_type=False),
            nullable=False,
            server_default="viewer",
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "INVITED", "ACTIVE", "SUSPENDED", "DEACTIVATED", "SOFT_DELETED",
                name="user_status", create_type=False,
            ),
            nullable=False,
            server_default="INVITED",
        ),
        sa.Column("password_hash", sa.Text, nullable=True),
        sa.Column("password_updated_at", TS, nullable=True),
        sa.Column("must_change_password", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("mfa_required", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("mfa_enrolled_at", TS, nullable=True),
        sa.Column("ver", sa.Integer, nullable=False, server_default="1"),
        sa.Column("failed_login_attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locked_until", TS, nullable=True),
        sa.Column("last_login_at", TS, nullable=True),
        sa.Column("last_login_ip", sa.Text, nullable=True),
        sa.Column("suspended_at", TS, nullable=True),
        sa.Column("suspended_reason", sa.Text, nullable=True),
        sa.Column("deleted_at", TS, nullable=True),
        sa.Column("deleted_by", UUID, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_status", "users", ["status"])
    op.create_index("ix_users_role", "users", ["role"])

    # ------------------------------------------------------------------
    # sessions (refresh-token family)
    # ------------------------------------------------------------------
    op.create_table(
        "sessions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("refresh_token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("parent_id", UUID, sa.ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("device_label", sa.Text, nullable=True),
        sa.Column("device_id", UUID, nullable=True),
        sa.Column("user_agent", sa.Text, nullable=False),
        sa.Column("ip_address", sa.Text, nullable=False),
        sa.Column("remember_me", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("last_used_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("revoked_reason", sa.Text, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    # ------------------------------------------------------------------
    # access_token_revocations — small, TTL by expires_at
    # ------------------------------------------------------------------
    op.create_table(
        "access_token_revocations",
        sa.Column("jti", sa.Text, primary_key=True),
        sa.Column("expires_at", TS, nullable=False),
    )
    op.create_index("ix_atr_expires_at", "access_token_revocations", ["expires_at"])

    # ------------------------------------------------------------------
    # login_history
    # ------------------------------------------------------------------
    op.create_table(
        "login_history",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("email", sa.Text, nullable=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ip_address", sa.Text, nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("device", sa.Text, nullable=True),
        sa.Column("location", sa.Text, nullable=True),
        sa.Column("outcome", sa.Text, nullable=False),  # ok|bad_password|no_user|locked|mfa_fail|mfa_ok
        sa.Column("fail_reason", sa.Text, nullable=True),
    )
    op.create_index("ix_login_history_email_at", "login_history", ["email", "at"])
    op.create_index("ix_login_history_ip_at", "login_history", ["ip_address", "at"])
    op.create_index("ix_login_history_user_at", "login_history", ["user_id", "at"])

    # ------------------------------------------------------------------
    # password_history
    # ------------------------------------------------------------------
    op.create_table(
        "password_history",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_password_history_user_created", "password_history", ["user_id", "created_at"])

    # ------------------------------------------------------------------
    # devices
    # ------------------------------------------------------------------
    op.create_table(
        "devices",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fingerprint", sa.Text, nullable=False, unique=True),
        sa.Column("device_name", sa.Text, nullable=False),
        sa.Column("device_type", sa.Text, nullable=True),
        sa.Column("browser", sa.Text, nullable=True),
        sa.Column("browser_version", sa.Text, nullable=True),
        sa.Column("os", sa.Text, nullable=True),
        sa.Column("os_version", sa.Text, nullable=True),
        sa.Column("ip_address", sa.Text, nullable=True),
        sa.Column("is_trusted", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("first_seen_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("last_seen_at", TS, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_devices_user_id", "devices", ["user_id"])

    # ------------------------------------------------------------------
    # MFA
    # ------------------------------------------------------------------
    op.execute(
        "CREATE TYPE mfa_kind AS ENUM ('TOTP', 'EMAIL', 'SMS', 'BACKUP')"
    )
    op.create_table(
        "mfa_factors",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM("TOTP", "EMAIL", "SMS", "BACKUP", name="mfa_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("label", sa.Text, nullable=True),
        sa.Column("secret_encrypted", sa.LargeBinary, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("last_used_at", TS, nullable=True),
        sa.Column("revoked_at", TS, nullable=True),
    )
    op.create_index("ix_mfa_factors_user_id", "mfa_factors", ["user_id"])

    op.create_table(
        "mfa_backup_codes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_hash", sa.Text, nullable=False),
        sa.Column("used_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_mfa_backup_user", "mfa_backup_codes", ["user_id"])

    op.create_table(
        "mfa_challenges",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_seed", sa.Text, nullable=False),
        sa.Column("factor_id", UUID, nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_mfa_challenges_user", "mfa_challenges", ["user_id"])

    # ------------------------------------------------------------------
    # invitations / email_verifications / password_resets
    # ------------------------------------------------------------------
    op.create_table(
        "invitations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM("admin", "operator", "viewer", name="user_role", create_type=False),
            nullable=False,
            server_default="viewer",
        ),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("invited_by_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("invited_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("accepted_at", TS, nullable=True),
        sa.Column("revoked_at", TS, nullable=True),
    )
    op.create_index("ix_invitations_email", "invitations", ["email"])

    op.create_table(
        "email_verifications",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("used_at", TS, nullable=True),
    )
    op.create_index("ix_email_verify_user", "email_verifications", ["user_id"])

    op.create_table(
        "password_resets",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("used_at", TS, nullable=True),
    )
    op.create_index("ix_password_reset_user", "password_resets", ["user_id"])

    # ------------------------------------------------------------------
    # api_keys (M5)
    # ------------------------------------------------------------------
    op.create_table(
        "api_keys",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("prefix", sa.Text, nullable=False),
        sa.Column("hash", sa.Text, nullable=False),
        sa.Column("scopes_json", postgresql.JSONB, nullable=False),
        sa.Column("created_by_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("last_used_at", TS, nullable=True),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("expires_at", TS, nullable=True),
    )
    op.create_index("ix_api_keys_created_by", "api_keys", ["created_by_id"])
    op.create_index("ix_api_keys_prefix", "api_keys", ["prefix"])

    # ------------------------------------------------------------------
    # audit_events (hash-chained)
    # ------------------------------------------------------------------
    op.create_table(
        "audit_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("actor_id", UUID, nullable=True),
        sa.Column("actor_type", sa.Text, nullable=False, server_default="user"),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("target_type", sa.Text, nullable=True),
        sa.Column("target_id", sa.Text, nullable=True),
        sa.Column("ip_address", sa.Text, nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("request_id", sa.Text, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False, server_default="success"),
        sa.Column("severity", sa.Text, nullable=False, server_default="INFO"),
        sa.Column("context_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("prev_hash", sa.Text, nullable=False),
        sa.Column("hash", sa.Text, nullable=False, unique=True),
    )
    op.create_index("ix_audit_at", "audit_events", ["at"])
    op.create_index("ix_audit_actor_at", "audit_events", ["actor_id", "at"])
    op.create_index("ix_audit_action_at", "audit_events", ["action", "at"])
    op.create_index("ix_audit_severity_at", "audit_events", ["severity", "at"])

    # ------------------------------------------------------------------
    # security_events (dashboard tail)
    # ------------------------------------------------------------------
    op.create_table(
        "security_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("user_id", UUID, nullable=True),
        sa.Column("ip_address", sa.Text, nullable=True),
        sa.Column("context_json", postgresql.JSONB, nullable=True),
    )
    op.create_index("ix_security_at", "security_events", ["at"])
    op.create_index("ix_security_kind_at", "security_events", ["kind", "at"])

    # ------------------------------------------------------------------
    # rate_limits (sliding-window buckets)
    # ------------------------------------------------------------------
    op.create_table(
        "rate_limit_buckets",
        sa.Column("bucket_key", sa.Text, primary_key=True),
        sa.Column("count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("expires_at", TS, nullable=False),
    )
    op.create_index("ix_rate_limits_expires", "rate_limit_buckets", ["expires_at"])

    # ------------------------------------------------------------------
    # mail_outbox (async retriable mail)
    # ------------------------------------------------------------------
    op.create_table(
        "mail_outbox",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("to_addr", sa.Text, nullable=False),
        sa.Column("template", sa.Text, nullable=False),
        sa.Column("vars_json", postgresql.JSONB, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("sent_at", TS, nullable=True),
        sa.Column("failed_at", TS, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
    )
    op.create_index("ix_mail_outbox_pending", "mail_outbox", ["sent_at", "next_attempt_at"])

    # ------------------------------------------------------------------
    # breach_cache (HIBP k-anon responses)
    # ------------------------------------------------------------------
    op.create_table(
        "breach_cache",
        sa.Column("prefix", sa.Text, primary_key=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("cached_at", TS, nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    """Drop everything the initial migration created."""
    for tbl in (
        "breach_cache",
        "mail_outbox",
        "rate_limit_buckets",
        "security_events",
        "audit_events",
        "api_keys",
        "password_resets",
        "email_verifications",
        "invitations",
        "mfa_challenges",
        "mfa_backup_codes",
        "mfa_factors",
        "devices",
        "password_history",
        "login_history",
        "access_token_revocations",
        "sessions",
        "users",
    ):
        op.drop_table(tbl)
    op.execute("DROP TYPE IF EXISTS mfa_kind")
    op.execute("DROP TYPE IF EXISTS user_status")
    op.execute("DROP TYPE IF EXISTS user_role")
