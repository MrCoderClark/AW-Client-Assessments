"""sso_allowlist — restrict Microsoft SSO sign-in to specific emails

When the `sso_allowlist_enabled` runtime setting is on, only emails in this
table may sign in via Entra (new or existing). Managed from Settings →
Access & Security. Local (/admin/login) accounts are unaffected.

Revision ID: b2c3d4e5f7a8
Revises: a1b2c3d4e5f7
Create Date: 2026-09-16 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2c3d4e5f7a8"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "sso_allowlist",
        sa.Column("email_normalized", sa.Text, primary_key=True),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("added_by", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("added_at", TS, nullable=False, server_default=sa.text("now()")),
    )
    op.execute(
        "INSERT INTO app_settings (key, value) VALUES ('sso_allowlist_enabled', 'false'::jsonb) "
        "ON CONFLICT (key) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM app_settings WHERE key = 'sso_allowlist_enabled'")
    op.drop_table("sso_allowlist")
