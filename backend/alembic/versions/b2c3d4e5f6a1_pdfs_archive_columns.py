"""pdfs archive columns

Three nullable columns + a partial index so archived rows can be moved
out of active date folders while remaining browsable, viewable, and
restorable. See docs/ARCHIVING_PLAN.md for the full flow.

- archived_at    : when the file was moved to the _Archive/ tree
- archive_path   : SMB path under _Archive/MM-DD-YYYY/... (NULL when active)
- archive_status : 'archived' (normal) | 'lost' (file confirmed missing) | NULL (active)

Partial index keeps the "list active PDFs" query cheap: the index only
covers the small archived subset so `WHERE archived_at IS NULL` scans
still hit the same table storage they always did.

Revision ID: b2c3d4e5f6a1
Revises: a1b2c3d4e5f6
Create Date: 2026-08-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a1"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.add_column("pdfs", sa.Column("archived_at", TS, nullable=True))
    op.add_column("pdfs", sa.Column("archive_path", sa.Text, nullable=True))
    op.add_column("pdfs", sa.Column("archive_status", sa.Text, nullable=True))
    op.execute(
        "CREATE INDEX ix_pdfs_archived_at ON pdfs (archived_at) "
        "WHERE archived_at IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_pdfs_archived_at")
    op.drop_column("pdfs", "archive_status")
    op.drop_column("pdfs", "archive_path")
    op.drop_column("pdfs", "archived_at")
