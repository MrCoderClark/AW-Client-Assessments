"""app tables from sqlite

Ports pdfs / pc_status / scan_runs / schedule from the old SQLite data.db
into Postgres. Same shape, native Postgres types.

Revision ID: 6c342edcdb59
Revises: 89986958c76a
Create Date: 2026-08-03 12:10:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6c342edcdb59"
down_revision: Union[str, Sequence[str], None] = "89986958c76a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    TS = sa.TIMESTAMP(timezone=True)

    op.create_table(
        "pdfs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("host", sa.Text, nullable=False),
        sa.Column("source_path", sa.Text, nullable=False),
        sa.Column("filename", sa.Text, nullable=False),
        sa.Column("proposed_name", sa.Text, nullable=True),
        sa.Column("assessment_type", sa.Text, nullable=True),
        sa.Column("first_name", sa.Text, nullable=True),
        sa.Column("last_name", sa.Text, nullable=True),
        sa.Column("size", sa.BigInteger, nullable=False),
        sa.Column("mtime", TS, nullable=False),
        sa.Column("md5", sa.Text, nullable=True),
        sa.Column("indexed_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("committed_at", TS, nullable=True),
        sa.Column("dest_path", sa.Text, nullable=True),
        sa.UniqueConstraint("host", "source_path", name="pdfs_host_source_uk"),
    )
    op.create_index("ix_pdfs_md5", "pdfs", ["md5"])
    op.create_index("ix_pdfs_assess", "pdfs", ["assessment_type"])
    op.create_index("ix_pdfs_lastname", "pdfs", ["last_name"])
    op.create_index("ix_pdfs_committed", "pdfs", ["committed_at"])

    op.create_table(
        "scan_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column("started_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("ended_at", TS, nullable=True),
        sa.Column("counts_json", postgresql.JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_index("ix_scan_runs_started", "scan_runs", ["started_at"])

    op.create_table(
        "pc_status",
        sa.Column("pc_name", sa.Text, primary_key=True),
        sa.Column("host", sa.Text, nullable=False),
        sa.Column("last_attempt", TS, nullable=True),
        sa.Column("last_seen", TS, nullable=True),
        sa.Column("last_reachable", sa.Boolean, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("last_counts_json", postgresql.JSONB, nullable=True),
    )

    op.create_table(
        "schedule",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("mode", sa.Text, nullable=False, server_default="scan"),
        sa.Column("time_of_day", sa.Text, nullable=False, server_default="02:00"),
        sa.Column("weekdays", sa.Text, nullable=False, server_default="0,1,2,3,4"),
        sa.Column("last_run_at", TS, nullable=True),
        sa.Column("last_run_ok", sa.Boolean, nullable=True),
        sa.Column("email_on_commit", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.CheckConstraint("id = 1", name="schedule_singleton_ck"),
    )
    op.execute("INSERT INTO schedule (id) VALUES (1) ON CONFLICT (id) DO NOTHING")


def downgrade() -> None:
    op.drop_table("schedule")
    op.drop_table("pc_status")
    op.drop_table("scan_runs")
    op.drop_table("pdfs")
