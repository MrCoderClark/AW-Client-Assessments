"""AuthContext — what middleware attaches to a request after auth.

Passed to permission checks and to services that need to know the caller.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ActorKind = Literal["user", "api_key", "anonymous", "system"]


@dataclass(frozen=True)
class AuthContext:
    actor_kind: ActorKind
    user_id: str | None = None       # for user + api_key (creator)
    session_id: str | None = None    # only for user
    api_key_id: str | None = None    # only for api_key
    role: str | None = None          # 'admin' | 'operator' | 'viewer' for user
    permissions: frozenset[str] = field(default_factory=frozenset)
    request_id: str = ""

    @property
    def is_authenticated(self) -> bool:
        return self.actor_kind in ("user", "api_key", "system")


ANONYMOUS = AuthContext(actor_kind="anonymous")
