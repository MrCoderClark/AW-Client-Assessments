"""Multi-location scoping foundation — locations, office aliases, location_id, user_locations

Milestone 1 of docs/MULTI_LOCATION.md. Additive only: creates the location tables
and stamps every existing PDF, PC, and user with Bronx (id 1) so the app behaves
exactly as before until scoping is switched on in later milestones.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-09-17 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    # --- locations (seed the five NYC offices; Bronx = 1 is the default) ---
    op.create_table(
        "locations",
        sa.Column("id", sa.SmallInteger, primary_key=True, autoincrement=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
    )
    op.execute(
        "INSERT INTO locations (id, code, name, active) VALUES "
        "(1,'bronx','Bronx',true), "
        "(2,'45th','45th Street',true), "
        "(3,'far_roc','Far Rockaway',true), "
        "(4,'jamaica','Jamaica',true), "
        "(5,'27th','27th Street',true)"
    )
    # keep the serial in step so admin-created locations get id 6+
    op.execute("SELECT setval(pg_get_serial_sequence('locations','id'), 5, true)")

    # --- office-string -> location alias map (admin-managed in the UI later) ---
    op.create_table(
        "location_office_aliases",
        sa.Column("office_value", sa.Text, primary_key=True),  # normalized lower/trim
        sa.Column("location_id", sa.SmallInteger,
                  sa.ForeignKey("locations.id"), nullable=False),
    )

    # --- location_id on PDFs and PC status (backfill to Bronx via default) ---
    op.add_column("pdfs", sa.Column(
        "location_id", sa.SmallInteger, sa.ForeignKey("locations.id"),
        nullable=False, server_default="1"))
    op.create_index("ix_pdfs_location_committed", "pdfs", ["location_id", "committed_at"])

    op.add_column("pc_status", sa.Column(
        "location_id", sa.SmallInteger, sa.ForeignKey("locations.id"),
        nullable=False, server_default="1"))

    # --- user <-> location (many-to-many; a user can cover several offices) ---
    op.create_table(
        "user_locations",
        sa.Column("user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("location_id", sa.SmallInteger,
                  sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("primary_loc", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("assigned_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("user_id", "location_id"),
    )
    # backfill: every existing user gets Bronx as their (primary) office
    op.execute(
        "INSERT INTO user_locations (user_id, location_id, primary_loc) "
        "SELECT id, 1, true FROM users"
    )


def downgrade() -> None:
    op.drop_table("user_locations")
    op.drop_column("pc_status", "location_id")
    op.drop_index("ix_pdfs_location_committed", table_name="pdfs")
    op.drop_column("pdfs", "location_id")
    op.drop_table("location_office_aliases")
    op.drop_table("locations")
