"""users.mfa_exempt + mfa_challenges email-OTP columns

- users.mfa_exempt: admin can exempt specific users from the 2FA requirement
  (docs/PHASE15_AUTH.md M3). Exempt users never get an MFA challenge unless
  they voluntarily enrolled a factor.
- mfa_challenges.purpose: 'verify' (user has TOTP) or 'enroll' (must set up).
- mfa_challenges.email_code_hash / email_code_expires_at: the emailed OTP
  (via Resend), so the email fallback needs no separate table.

Revision ID: a1b2c3d4e5f7
Revises: f6a1b2c3d4e5
Create Date: 2026-09-16 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, Sequence[str], None] = "f6a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.add_column("users", sa.Column(
        "mfa_exempt", sa.Boolean, nullable=False, server_default=sa.text("false")))
    op.add_column("mfa_challenges", sa.Column("purpose", sa.Text, nullable=True))
    op.add_column("mfa_challenges", sa.Column("email_code_hash", sa.Text, nullable=True))
    op.add_column("mfa_challenges", sa.Column("email_code_expires_at", TS, nullable=True))


def downgrade() -> None:
    op.drop_column("mfa_challenges", "email_code_expires_at")
    op.drop_column("mfa_challenges", "email_code_hash")
    op.drop_column("mfa_challenges", "purpose")
    op.drop_column("users", "mfa_exempt")
