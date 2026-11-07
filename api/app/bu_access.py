"""Server-owned business-unit grants and projections for the aggregation API."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

import yaml

from .security import AUTHENTICATED, ROLE_PERMISSIONS, Access, SecurityConfig, SecurityDenied

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}\Z")
_POLICY_FIELDS = {"version", "groups", "resources", "people", "identities", "shadow_teams", "secure_resources",
                  "portfolio_roles", "portfolio_agents", "portfolio_autonomy"}
PORTFOLIO_ROLES = frozenset({"project_lead", "manager", "architect"})
PORTFOLIO_AGENT_MODES = ("read_only", "prepare", "sensitive")
PORTFOLIO_AUTONOMY_LEVELS = frozenset({"autonomous", "human_review", "blocked"})


class _UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError("invalid or duplicate business policy key")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("invalid business policy identifier")
    return value


def _list(value: Any, *, wildcard: bool = False) -> frozenset[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 256:
        raise ValueError("invalid business policy assignment")
    if wildcard and value == ["*"]:
        return frozenset({"*"})
    items = frozenset(_identifier(item) for item in value)
    if len(items) != len(value):
        raise ValueError("duplicate business policy assignment")
    return items


def _mapping(value: Any, *, wildcard: bool = False) -> dict[str, frozenset[str]]:
    if not isinstance(value, dict) or len(value) > 512:
        raise ValueError("invalid business policy mapping")
    return {_identifier(key): _list(items, wildcard=wildcard) for key, items in value.items()}


def _bindings(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 512:
        raise ValueError("invalid business policy bindings")
    result = {_identifier(subject): _identifier(item) for subject, item in value.items()}
    if len(set(result.values())) != len(result):
        raise ValueError("multiple subjects for an identity")
    return result


def _autonomy(value: Any, known: set[str]) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict) or not value.keys() <= known:
        raise ValueError("invalid portfolio autonomy units")
    for modes in value.values():
        if (not isinstance(modes, dict) or not modes.keys() <= set(PORTFOLIO_AGENT_MODES)
                or any(not isinstance(level, str) or level not in PORTFOLIO_AUTONOMY_LEVELS
                       for level in modes.values())):
            raise ValueError("invalid portfolio autonomy mode")
    return value


@dataclass(frozen=True)
class BUScope:
    centers: frozenset[str]
    all_units: bool
    shadow_teams: frozenset[str]
    global_admin: bool

    def allows(self, center: Any) -> bool:
        return self.all_units or (isinstance(center, str) and center in self.centers)


@dataclass(frozen=True)
class BusinessPolicy:
    groups: Mapping[str, frozenset[str]]
    resources: Mapping[str, frozenset[str]]
    people: Mapping[str, frozenset[str]]
    identities: Mapping[str, str]
    shadow_teams: Mapping[str, frozenset[str]]
    secure_resources: frozenset[str]
    fingerprint: str
    portfolio_roles: Mapping[str, frozenset[str]] = field(default_factory=dict)
    portfolio_agents: Mapping[str, str] = field(default_factory=dict)
    portfolio_autonomy: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str, cfg: dict[str, Any], security: SecurityConfig) -> BusinessPolicy:
        try:
            with Path(path).open("rb") as source:
                raw = source.read(65537)
            if len(raw) > 65536:
                raise ValueError("oversized business policy")
            if any(isinstance(event, yaml.AliasEvent) for event in yaml.parse(raw)):
                raise ValueError("aliases are not allowed in business policy")
            doc = yaml.load(raw, Loader=_UniqueLoader)
            if (not isinstance(doc, dict) or set(doc) - _POLICY_FIELDS or type(doc.get("version")) is not int
                    or doc["version"] != 1):
                raise ValueError("invalid business policy version or fields")
            groups = _mapping(doc.get("groups"), wildcard=True)
            resources = _mapping(doc.get("resources", {}), wildcard=True)
            people = _mapping(doc.get("people", {}))
            teams = _mapping(doc.get("shadow_teams", {}))
            secure = _list(doc["secure_resources"]) if "secure_resources" in doc else frozenset()
            identities = _bindings(doc.get("identities", {}))
            portfolio_roles = _mapping(doc.get("portfolio_roles", {}))
            portfolio_agents = _bindings(doc.get("portfolio_agents", {}))
            known = {row.get("id") for row in (cfg.get("factory") or {}).get("cost_centers") or []}
            portfolio_autonomy = _autonomy(doc.get("portfolio_autonomy", {}), known)
            roster = {row.get("id") for row in (cfg.get("profiles") or {}).get("people") or []}
            agents = {row.get("id") for row in (cfg.get("catalog") or {}).get("agents") or []}
            mapped = {group for group, _ in security.role_map}
            if (not groups or not set(groups) <= mapped or not security.trusted_networks or not security.enforced
                    or not (set(people) | set(teams)) <= known or not secure <= resources.keys()):
                raise ValueError("unknown group, center or secure resource")
            if any(not assigned <= known | {"*"} for assigned in (*groups.values(), *resources.values())):
                raise ValueError("unknown center assignment")
            if not set(identities.values()) <= roster or any(not values <= roster for values in people.values()):
                raise ValueError("unknown booking profile")
            if (not portfolio_roles.keys() <= groups.keys()
                    or any(not roles <= PORTFOLIO_ROLES for roles in portfolio_roles.values())
                    or identities.keys() & portfolio_agents.keys()
                    or not set(portfolio_agents.values()) <= agents):
                raise ValueError("unknown or ambiguous portfolio grant")
            if len(set().union(*teams.values())) != sum(map(len, teams.values())):
                raise ValueError("ambiguous Shadow team scope")
            return cls(groups, resources, people, identities, teams, secure, hashlib.sha256(raw).hexdigest(),
                       portfolio_roles, portfolio_agents, portfolio_autonomy)
        except (OSError, yaml.YAMLError, ValueError, TypeError, RecursionError):
            raise ValueError("invalid FACTORY_BU_POLICY configuration") from None

    def scope(self, access: Access, security: SecurityConfig, *, groups: frozenset[str] | None = None,
              permission: str | None = None) -> BUScope | None:
        principal = access.principal
        permission = permission or access.permission
        memberships = set(principal.groups) if principal.authenticated else set()
        if groups is not None:
            memberships.intersection_update(groups)
        grants = [
            (self.groups[group], role) for group, role in security.role_map
            if group in memberships and group in self.groups
            and (permission == AUTHENTICATED or permission in ROLE_PERMISSIONS[role])
        ]
        if not grants:
            return None
        centers = frozenset().union(*(assigned for assigned, _ in grants))
        global_admin = any("*" in assigned and role == "admin" for assigned, role in grants)
        teams = frozenset().union(*(self.shadow_teams.get(center, ()) for center in centers))
        return BUScope(centers - {"*"}, "*" in centers, teams, global_admin)

    def members(self, scope: BUScope) -> frozenset[str]:
        centers = self.people if scope.all_units else scope.centers
        return frozenset().union(*(self.people.get(center, ()) for center in centers))

    def resource_allowed(self, resource: Any, center: Any) -> bool:
        if not isinstance(resource, str) or not isinstance(center, str):
            return False
        units = self.resources.get(resource, ())
        return "*" in units or center in units

    def scoped_role(self, access: Access, security: SecurityConfig, center: str) -> Access:
        for group, role in security.role_map:
            units = self.groups.get(group, ())
            if group in access.principal.groups and (center in units or "*" in units) \
                    and access.permission in ROLE_PERMISSIONS[role]:
                return replace(access, role=role)
        raise SecurityDenied(403, "business_scope_forbidden", "auth.forbidden")
