"""pdfs.text_hash for content dedupe

SHA-256 of normalized extracted PDF text. Used alongside md5 so that
byte-different but content-identical PDFs (common when the vendor
embeds a fresh /CreationDate on every download) still dedupe.

Revision ID: 56bd941a6dbd
Revises: 6c342edcdb59
Create Date: 2026-08-03 13:30:04.354673
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "56bd941a6dbd"
down_revision: Union[str, Sequence[str], None] = "6c342edcdb59"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pdfs", sa.Column("text_hash", sa.Text, nullable=True))
    op.create_index("ix_pdfs_text_hash", "pdfs", ["text_hash"])


def downgrade() -> None:
    op.drop_index("ix_pdfs_text_hash", table_name="pdfs")
    op.drop_column("pdfs", "text_hash")
