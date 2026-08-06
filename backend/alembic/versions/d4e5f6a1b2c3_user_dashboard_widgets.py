"""per-user dashboard widget overrides

Adds `users.dashboard_widgets` (nullable JSONB array of widget keys). When
NULL, the frontend renders the dashboard component tied to the user's
profile.layout_key. When set, it renders a generic grid from the widget
registry using the given order.

Revision ID: d4e5f6a1b2c3
Revises: c3d4e5f6a1b2
Create Date: 2026-08-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e5f6a1b2c3"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("dashboard_widgets", postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "dashboard_widgets")
