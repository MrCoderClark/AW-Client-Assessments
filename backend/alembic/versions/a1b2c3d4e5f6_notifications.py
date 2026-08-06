"""notifications table

Per-user notification feed with unread state. Fanout on emit: one row per
recipient. At 3–10 real users this is fine; a shared table + read-cursor is
the upgrade path if a category ever multicasts to hundreds of recipients.

Revision ID: a1b2c3d4e5f6
Revises: 56bd941a6dbd
Create Date: 2026-08-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "56bd941a6dbd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
TS = postgresql.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "user_id", UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("category", sa.Text, nullable=False),   # scan_commit | security | user_lifecycle | pc_health
        sa.Column("kind", sa.Text, nullable=False),        # scan_completed | lockout_started | ...
        sa.Column("severity", sa.Text, nullable=False, server_default="INFO"),  # INFO | WARN | SEC
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("url", sa.Text, nullable=True),
        sa.Column("context_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("read_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_notifications_user_recent", "notifications",
        ["user_id", sa.text("created_at DESC")],
    )
    op.execute(
        "CREATE INDEX ix_notifications_user_unread ON notifications (user_id) "
        "WHERE read_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_notifications_user_unread")
    op.drop_index("ix_notifications_user_recent", table_name="notifications")
    op.drop_table("notifications")
