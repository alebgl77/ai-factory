"""Identity contract for trusted Python hosts and the existing HTTP server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

READ = "read"
ASSESSMENT = "registry.assessment"
ROLES = ("viewer", "editor", "maintainer", "approver", "admin")


class WorkActor(Protocol):
    @property
    def actor(self) -> str: ...
    @property
    def roles(self) -> frozenset[str]: ...
    @property
    def authenticated(self) -> bool: ...
    @property
    def ids(self) -> frozenset[str]: ...
    def role_for(self, permission: str) -> str | None: ...


@dataclass(frozen=True)
class Actor:
    """Verified identity and role claims supplied by a trusted embedding host."""

    actor: str
    roles: frozenset[str]
    authenticated: bool = True
    subject: str | None = None
    username: str | None = None

    def __post_init__(self) -> None:
        if any(value is not None and (
            not isinstance(value, str) or not value or value != value.strip() or len(value) > 256
            or not value.isprintable()
        ) for value in (self.actor, self.subject, self.username)) or self.actor is None:
            raise ValueError("invalid actor identity")
        if (not isinstance(self.roles, frozenset) or not self.roles.issubset(ROLES)
                or not isinstance(self.authenticated, bool)):
            raise ValueError("invalid actor claims")

    @property
    def ids(self) -> frozenset[str]:
        aliases = {value for value in (self.actor, self.subject, self.username) if value}
        return frozenset(aliases | {value.lower() for value in aliases if "@" in value})

    def role_for(self, permission: str) -> str | None:
        roles = ROLES if permission == READ else ("approver", "admin") if permission == ASSESSMENT else ()
        return next((role for role in roles if role in self.roles), None)
