"""Add 'corporate_rep' to the user_role enum

Corporate Rep = a viewer who may also push assessments to Salesforce
(perms: viewer set + salesforce:read + salesforce:push). See auth/permissions.py.

Revision ID: d1e2f3a4b5c6
Revises: c7f3a1e9b2d4
Create Date: 2026-09-17 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c7f3a1e9b2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres 12+ allows ADD VALUE inside a transaction (we don't use it here).
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'corporate_rep'")


def downgrade() -> None:
    # Postgres can't drop an enum value; leaving it is harmless.
    pass
