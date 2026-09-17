"""app_settings — runtime key/value config toggled from the UI

A tiny JSONB key/value store for settings an admin flips at runtime (as
opposed to env vars, which need a restart). First keys:
  - sso_login_enabled : when false, Microsoft SSO sign-in is blocked and only
                        local accounts may log in (docs/ENTRA_SSO.md).
  - mfa_required      : when true, all users must enrol app 2FA on next login.

ponytail: one generic JSONB table beats a column-per-setting migration treadmill
at this scale; the app layer owns the schema of each value.

Revision ID: f6a1b2c3d4e5
Revises: e5f6a1b2c3d4
Create Date: 2026-09-16 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "e5f6a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("value", postgresql.JSONB, nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_by", postgresql.UUID(as_uuid=False), nullable=True),
    )
    # Seed defaults so a fresh install behaves as documented.
    op.execute(
        "INSERT INTO app_settings (key, value) VALUES "
        "('sso_login_enabled', 'true'::jsonb), "
        "('mfa_required', 'false'::jsonb)"
    )


def downgrade() -> None:
    op.drop_table("app_settings")
