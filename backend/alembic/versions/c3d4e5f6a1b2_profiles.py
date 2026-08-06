"""dashboard profiles

Salesforce-style profile assigned to each user. For M1, a profile is just
a name + description + a `layout_key` that maps to a code-defined React
dashboard component. Custom widget layouts remain out of scope.

Seeds three system profiles (`is_system = true`) — Operations, Fleet
Health, Records — matching the three layout components shipped in the
frontend. System rows cannot be renamed or deleted.

Revision ID: c3d4e5f6a1b2
Revises: b2c3d4e5f6a1
Create Date: 2026-08-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3d4e5f6a1b2"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=False)
TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("layout_key", sa.Text, nullable=False),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.text("now()")),
    )

    op.add_column(
        "users",
        sa.Column(
            "profile_id",
            UUID,
            sa.ForeignKey("profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_users_profile_id", "users", ["profile_id"])

    # Seed the three system profiles. gen_random_uuid() is available in
    # Postgres 13+ from pgcrypto (installed by default on modern PG).
    op.execute(
        """
        INSERT INTO profiles (id, name, description, layout_key, is_system)
        VALUES
          (gen_random_uuid(), 'Operations',   'Scan + commit workflow for daily operators.', 'ops_default',   true),
          (gen_random_uuid(), 'Fleet Health', 'PC-first view for IT and infrastructure.',    'fleet_health', true),
          (gen_random_uuid(), 'Records',      'Read-only file summary for reviewers.',       'records',      true)
        """
    )


def downgrade() -> None:
    op.drop_index("ix_users_profile_id", table_name="users")
    op.drop_column("users", "profile_id")
    op.drop_table("profiles")
