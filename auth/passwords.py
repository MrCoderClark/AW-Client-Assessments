"""PasswordService — Argon2id hash/verify + complexity check.

M3 adds: HIBP k-anon breach check, password history (N=12), expiration
policy, zxcvbn strength score. M1 only enforces length + character-class
diversity — enough to reject "password123" but not weak variants.
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from .settings import AuthSettings


class PasswordComplexityError(ValueError):
    """Raised when a proposed password doesn't meet complexity rules."""


class PasswordService:
    def __init__(self, settings: AuthSettings) -> None:
        self.s = settings
        self._ph = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_kib,
            parallelism=settings.argon2_parallelism,
        )
        # Dummy hash used to keep timing constant when the user doesn't exist.
        # ponytail: hash("no-such-user") once at boot; verify against it in the
        # user-not-found branch so response time doesn't leak user existence.
        self._dummy_hash = self._ph.hash("no-such-user-placeholder")

    def hash(self, password: str) -> str:
        return self._ph.hash(password)

    def verify(self, password: str, encoded: str) -> bool:
        try:
            self._ph.verify(encoded, password)
            return True
        except (VerifyMismatchError, InvalidHashError):
            return False

    def verify_against_dummy(self, password: str) -> None:
        """Run Argon2 verify against a constant hash so the timing matches
        the real-user path. Used in the user-not-found branch."""
        try:
            self._ph.verify(self._dummy_hash, password)
        except (VerifyMismatchError, InvalidHashError):
            pass

    def needs_rehash(self, encoded: str) -> bool:
        """True when Argon2 params have moved since the hash was created."""
        return self._ph.check_needs_rehash(encoded)

    def check_complexity(self, password: str) -> None:
        """Raise PasswordComplexityError with a human message on failure."""
        if len(password) < 12:
            raise PasswordComplexityError("Password must be at least 12 characters.")
        classes = 0
        if any(c.islower() for c in password):
            classes += 1
        if any(c.isupper() for c in password):
            classes += 1
        if any(c.isdigit() for c in password):
            classes += 1
        if any(not c.isalnum() for c in password):
            classes += 1
        if classes < 3:
            raise PasswordComplexityError(
                "Password must contain at least 3 of: lowercase, uppercase, digits, symbols."
            )
