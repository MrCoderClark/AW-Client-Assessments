"""users SSO identity columns (Microsoft Entra)

Adds the linkage between a CFV user row and its external Entra identity.
See docs/ENTRA_SSO.md §8.

- sso_provider: "entra" (kept generic for a possible second IdP later)
- sso_subject:  the immutable Entra object id (`oid` claim)
- sso_tenant:   the Entra tenant id (`tid` claim)

Partial unique index guarantees one CFV account per external identity while
leaving password-only rows (sso_subject IS NULL) unconstrained. password_hash
is already nullable (89986958c76a), so SSO-only users need no change there.

ponytail: two/three columns beat a normalized external_identities table while
there is exactly one IdP. Upgrade path is documented in ENTRA_SSO.md §8.

Revision ID: e5f6a1b2c3d4
Revises: d4e5f6a1b2c3
Create Date: 2026-09-16 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a1b2c3d4"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("sso_provider", sa.Text, nullable=True))
    op.add_column("users", sa.Column("sso_subject", sa.Text, nullable=True))
    op.add_column("users", sa.Column("sso_tenant", sa.Text, nullable=True))
    op.create_index(
        "ux_users_sso",
        "users",
        ["sso_provider", "sso_subject"],
        unique=True,
        postgresql_where=sa.text("sso_subject IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_users_sso", table_name="users")
    op.drop_column("users", "sso_tenant")
    op.drop_column("users", "sso_subject")
    op.drop_column("users", "sso_provider")
