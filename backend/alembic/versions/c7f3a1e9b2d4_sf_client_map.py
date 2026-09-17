"""sf_client_map — learned client ↔ Salesforce case number ↔ Account mapping

A rep confirms a client's Salesforce case number once (resolved to a Person
Account). We remember it here so later assessments for that client auto-target
the same record. Keyed on case_number (the reliable unique key); name_key is a
soft index for prefilling the case number from a PDF's extracted name.

See docs/SALESFORCE_INTEGRATION.md.

Revision ID: c7f3a1e9b2d4
Revises: b2c3d4e5f7a8
Create Date: 2026-09-17 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7f3a1e9b2d4"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "sf_client_map",
        sa.Column("case_number", sa.Text, primary_key=True),
        sa.Column("account_id", sa.Text, nullable=False),
        sa.Column("account_name", sa.Text, nullable=True),
        sa.Column("name_key", sa.Text, nullable=True),
        sa.Column("first_name", sa.Text, nullable=True),
        sa.Column("last_name", sa.Text, nullable=True),
        sa.Column("confirmed_by", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("confirmed_at", TS, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_sf_client_map_name_key", "sf_client_map", ["name_key"])


def downgrade() -> None:
    op.drop_index("ix_sf_client_map_name_key", table_name="sf_client_map")
    op.drop_table("sf_client_map")
