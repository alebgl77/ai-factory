"""Application security controls of the aggregation API (MIT).

Identity (``FACTORY_AUTH_MODE``)
    ``none``  demo default: nobody is authenticated, every request acts as role ``admin``; the
              posture is flagged in ``/api/v1/health`` and ``meta.security`` (status ``demo``).
    ``proxy`` an SSO proxy in front of the API (oauth2-proxy or any equivalent) authenticates the
              user and forwards ``X-Forwarded-User`` / ``X-Forwarded-Email`` / ``X-Forwarded-Groups``.
              Those headers are read **only** when the TCP peer is in ``FACTORY_TRUSTED_PROXIES``;
              every other request to ``/api/*`` (except ``/api/v1/health``) is refused with 401.
    Any other value fails closed: it is treated as ``proxy`` and reported as a warning.

Authorisation (RBAC)
    Roles ``viewer``, ``editor``, ``maintainer``, ``approver``, ``admin`` (:data:`ROLE_PERMISSIONS`),
    granted from SSO groups by ``FACTORY_ROLE_MAP`` (``"ai-governance:approver,staff:editor"``) and
    optionally ``FACTORY_DEFAULT_ROLE``. Duties stay separate: a maintainer records model versions,
    an approver reviews them, and the same identity can never approve a version it recorded
    (four-eyes, enforced in ``StateStore.record_assessment`` under the state lock).

Also here: per actor/IP token-bucket rate limiting of writes (429 + ``Retry-After``), request ids
(``X-Request-ID``), security response headers, structured JSON security log lines for every
denied request and the security posture published by ``/api/v1/health``.
"""

from __future__ import annotations

import ipaddress
import logging
import math
import re
import threading
import time
import uuid
from collections import Counter, OrderedDict
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import seclog
from .audit import AuditContext
from .i18n import msg, normalize_lang

AUTH_MODES = ("none", "proxy")
ROLES = ("viewer", "editor", "maintainer", "approver", "admin")

READ = "read"
SPACES_WRITE = "spaces.write"
PLACEMENT_DECIDE = "placement.decide"
MODEL_VERSION = "registry.model_version"
ASSESSMENT = "registry.assessment"
AUDIT_READ = "audit.read"
AUTHENTICATED = "authenticated"  # pseudo-permission: any authenticated identity, no role needed
PERMISSIONS = (READ, SPACES_WRITE, PLACEMENT_DECIDE, MODEL_VERSION, ASSESSMENT, AUDIT_READ)

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset({READ}),
    "editor": frozenset({READ, SPACES_WRITE, PLACEMENT_DECIDE}),
    "maintainer": frozenset({READ, MODEL_VERSION}),
    "approver": frozenset({READ, ASSESSMENT, AUDIT_READ}),
    "admin": frozenset(PERMISSIONS),
}

APPROVING_DECISIONS = frozenset({"approved", "approved_with_conditions"})
ANONYMOUS = "anonymous"
PUBLIC_API_PATHS = frozenset({"/api/v1/health"})  # liveness probe: no identity, no secret

H_USER, H_EMAIL, H_GROUPS = b"x-forwarded-user", b"x-forwarded-email", b"x-forwarded-groups"
IDENTITY_HEADERS = (H_USER, H_EMAIL, H_GROUPS)
MAX_IDENTITY_LEN = 256
MAX_GROUPS = 100
_SAFE_VALUE = re.compile(r"^[^\x00-\x1f\x7f]+$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_PEER = re.compile(r"^[A-Za-z0-9.:_-]{1,64}$")

# JSON never needs to load anything: the strictest policy, including against framing.
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"


def roles_granting(permission: str) -> list[str]:
    return [r for r in ROLES if permission in ROLE_PERMISSIONS[r]]


# ============================================================================== configuration
Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(frozen=True)
class SecurityConfig:
    """Validated security settings. Invalid items are dropped (fail closed) and reported."""

    auth_mode: str = "none"                       # effective mode: none | proxy
    configured_mode: str = "none"
    trusted_networks: tuple[Network, ...] = ()
    role_map: tuple[tuple[str, str], ...] = ()    # (group, role)
    default_role: str | None = None
    rate_limit_per_min: int = 60
    rate_limit_burst: int = 20
    max_body_bytes: int = 64 * 1024
    cors_allow_list: bool = False
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_settings(cls, settings: Any) -> "SecurityConfig":
        warnings: list[str] = []
        configured = str(getattr(settings, "auth_mode", "none") or "none").strip().lower()
        mode = configured
        if mode not in AUTH_MODES:
            mode = "proxy"  # fail closed: an unknown mode never opens the API
            warnings.append("auth_mode_invalid")
        networks: list[Network] = []
        for item in getattr(settings, "trusted_proxies", ()) or ():
            try:
                net = ipaddress.ip_network(str(item).strip(), strict=False)
            except ValueError:
                warnings.append("trusted_proxy_invalid")
                continue
            if net.prefixlen == 0:  # 0.0.0.0/0 or ::/0 would let anyone assert an identity
                warnings.append("trusted_proxy_any_refused")
                continue
            networks.append(net)
        role_map: list[tuple[str, str]] = []
        for item in getattr(settings, "role_map", ()) or ():
            group, sep, role = str(item).rpartition(":")
            group, role = group.strip(), role.strip().lower()
            if not sep or not group or role not in ROLES:
                warnings.append("role_map_invalid")
                continue
            role_map.append((group, role))
        default_role = getattr(settings, "default_role", None)
        if default_role is not None:
            default_role = str(default_role).strip().lower() or None
        if default_role is not None and default_role not in ROLES:
            warnings.append("default_role_invalid")
            default_role = None
        rate = max(0, int(getattr(settings, "rate_limit_per_min", 60) or 0))
        burst = max(1, int(getattr(settings, "rate_limit_burst", 20) or 1))
        if mode == "none":
            warnings.append("auth_disabled")
        else:
            if not networks:
                warnings.append("no_trusted_proxy")
            if not role_map and default_role is None:
                warnings.append("no_role_mapping")
        if rate == 0:
            warnings.append("rate_limit_disabled")
        return cls(auth_mode=mode, configured_mode=configured, trusted_networks=tuple(networks),
                   role_map=tuple(role_map), default_role=default_role, rate_limit_per_min=rate,
                   rate_limit_burst=burst, max_body_bytes=int(getattr(settings, "max_body_bytes", 65536)),
                   cors_allow_list=bool(getattr(settings, "cors_origins", ())),
                   warnings=tuple(dict.fromkeys(warnings)))

    @property
    def enforced(self) -> bool:
        return self.auth_mode == "proxy"

    def trusts(self, address: str | None) -> bool:
        ip = _parse_ip(address)
        return ip is not None and any(ip in net for net in self.trusted_networks)

    def roles_for(self, groups: Iterable[str]) -> frozenset[str]:
        groups = set(groups)
        roles = {role for group, role in self.role_map if group in groups}
        if self.default_role:
            roles.add(self.default_role)
        return frozenset(roles)


def _parse_ip(value: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    if not value:
        return None
    try:
        ip = ipaddress.ip_address(value.strip().strip("[]"))
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


# ============================================================================== identity
@dataclass(frozen=True)
class Principal:
    actor: str
    roles: frozenset[str]
    authenticated: bool
    auth_mode: str
    user: str | None = None
    email: str | None = None
    groups: tuple[str, ...] = ()

    @property
    def ids(self) -> frozenset[str]:
        """Identity keys compared by the four-eyes rule (user id as sent, e-mail lower-cased)."""
        return frozenset(v for v in (self.user, self.email.lower() if self.email else None) if v)

    def role_for(self, permission: str) -> str | None:
        """Least-privileged role of the principal that grants ``permission`` (None: denied)."""
        if permission == AUTHENTICATED:
            return next((r for r in ROLES if r in self.roles), "none")
        return next((r for r in ROLES if r in self.roles and permission in ROLE_PERMISSIONS[r]), None)

    def permissions(self) -> list[str]:
        return [p for p in PERMISSIONS if self.role_for(p)]


@dataclass(frozen=True)
class Access:
    """Result of an authorisation check, handed to the route."""

    principal: Principal
    role: str
    permission: str
    request_id: str
    client_ip: str

    def audit_context(self) -> AuditContext:
        p = self.principal
        return AuditContext(actor=p.actor, role=self.role, auth_mode=p.auth_mode, request_id=self.request_id,
                            client_ip=self.client_ip, ids=p.ids, authenticated=p.authenticated)


class SecurityDenied(Exception):
    """A refused request: rendered as ``{detail, code}`` with a localised ``detail``."""

    def __init__(self, status: int, code: str, key: str, params: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.key = key
        self.params = params or {}
        self.headers = headers or {}

    def response(self, lang: str) -> JSONResponse:
        return JSONResponse(status_code=self.status, headers=self.headers,
                            content={"detail": msg(lang, self.key, **self.params), "code": self.code})


def _headers(scope: Scope) -> list[tuple[bytes, bytes]]:
    return list(scope.get("headers") or [])


def _peer(scope: Scope) -> str | None:
    client = scope.get("client")
    return str(client[0]) if client else None


def _safe_peer(value: str | None) -> str:
    return value if value and _SAFE_PEER.match(value) else "unknown"


# ============================================================================== rate limiting
class RateLimiter:
    """In-memory token buckets keyed by actor or client IP (bounded LRU, thread-safe)."""

    def __init__(self, per_minute: int, burst: int, clock: Callable[[], float] = time.monotonic,
                 max_keys: int = 10_000) -> None:
        self.rate = per_minute / 60.0
        self.burst = float(max(1, burst))
        self.enabled = per_minute > 0
        self.clock = clock
        self.max_keys = max_keys
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._lock = threading.Lock()

    def take(self, key: str) -> float:
        """Consume one token; return 0 when allowed, else the seconds to wait."""
        if not self.enabled:
            return 0.0
        now = self.clock()
        with self._lock:
            tokens, last = self._buckets.pop(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens >= 1.0:
                tokens -= 1.0
                wait = 0.0
            else:
                wait = (1.0 - tokens) / self.rate
            self._buckets[key] = (tokens, now)
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)
        return wait


# ============================================================================== guard
class Guard:
    """Authentication, authorisation, throttling and denial accounting for one application."""

    def __init__(self, config: SecurityConfig, clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config
        self.limiter = RateLimiter(config.rate_limit_per_min, config.rate_limit_burst, clock)
        self._denials: Counter[str] = Counter()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ client address
    def client_ip(self, scope: Scope) -> str:
        """Client address: the TCP peer, or the right-most untrusted X-Forwarded-For hop when the
        peer is a trusted proxy (so a client can never choose its own address)."""
        peer = _peer(scope)
        if not self.config.trusts(peer):
            return _safe_peer(peer)
        hops: list[str] = []
        for name, value in _headers(scope):
            if name == b"x-forwarded-for":
                hops += [h.strip() for h in value.decode("latin-1").split(",") if h.strip()]
        for hop in reversed(hops):
            ip = _parse_ip(hop)
            if ip is None:
                break  # malformed hop: stop at the last address we can vouch for
            if not self.config.trusts(str(ip)):
                return str(ip)
        return _safe_peer(peer)

    # ------------------------------------------------------------------ authentication
    def authenticate(self, scope: Scope) -> Principal:
        cfg = self.config
        if not cfg.enforced:
            # Demo mode: identity headers are ignored whatever their source.
            return Principal(actor=ANONYMOUS, roles=frozenset({"admin"}), authenticated=False, auth_mode="none")
        peer = _peer(scope)
        values: dict[bytes, list[str]] = {h: [] for h in IDENTITY_HEADERS}
        for name, value in _headers(scope):
            if name in values:
                values[name].append(value.decode("latin-1"))
        if not cfg.trusts(peer):
            raise self.deny(scope, 401, "untrusted_source", "auth.untrusted_source",
                            forwarded_identity_present=any(values.values()))
        if any(len(v) > 1 for v in values.values()):
            raise self.deny(scope, 401, "invalid_identity", "auth.invalid_identity", detail="duplicate_header")
        raw_user = values[H_USER][0].strip() if values[H_USER] else ""
        raw_email = values[H_EMAIL][0].strip() if values[H_EMAIL] else ""
        user = self._clean(raw_user) if raw_user else None   # an empty header counts as absent
        email = self._clean(raw_email) if raw_email else None
        if (raw_user and user is None) or (raw_email and email is None):
            raise self.deny(scope, 401, "invalid_identity", "auth.invalid_identity", detail="malformed_value")
        if not user and not email:
            raise self.deny(scope, 401, "unauthenticated", "auth.unauthenticated")
        groups: list[str] = []
        if values[H_GROUPS]:
            raw = values[H_GROUPS][0]
            groups = [g.strip() for g in raw.split(",") if g.strip()]
            if len(groups) > MAX_GROUPS or any(self._clean(g) is None for g in groups):
                raise self.deny(scope, 401, "invalid_identity", "auth.invalid_identity", detail="malformed_groups")
        email = email.lower() if email else None
        return Principal(actor=email or user or ANONYMOUS, roles=cfg.roles_for(groups), authenticated=True,
                         auth_mode="proxy", user=user, email=email, groups=tuple(groups))

    @staticmethod
    def _clean(value: str) -> str | None:
        value = value.strip()
        if not value or len(value) > MAX_IDENTITY_LEN or not _SAFE_VALUE.match(value):
            return None
        return value

    # ------------------------------------------------------------------ authorisation
    def authorize(self, scope: Scope, principal: Principal, permission: str) -> str:
        role = principal.role_for(permission)
        if role is None:
            raise self.deny(scope, 403, "forbidden", "auth.forbidden", principal=principal, permission=permission,
                            params={"permission": permission, "roles": ", ".join(roles_granting(permission))})
        return role

    def throttle(self, scope: Scope, principal: Principal) -> None:
        key = f"actor:{principal.actor}" if principal.authenticated else f"ip:{self.client_ip(scope)}"
        wait = self.limiter.take(key)
        if wait > 0:
            seconds = max(1, math.ceil(wait))
            raise self.deny(scope, 429, "rate_limited", "auth.rate_limited", principal=principal,
                            params={"seconds": seconds}, headers={"Retry-After": str(seconds)})

    # ------------------------------------------------------------------ denials
    def deny(self, scope: Scope, status: int, code: str, key: str, *, principal: Principal | None = None,
             permission: str | None = None, params: dict[str, Any] | None = None,
             headers: dict[str, str] | None = None, **fields: Any) -> SecurityDenied:
        """Count and log a denial (JSON line on ``factory.security``) and build the exception."""
        with self._lock:
            self._denials[code] += 1
        headers = dict(headers or {})
        if status == 401:  # RFC 9110: a 401 carries a challenge; identity comes from the SSO proxy
            headers.setdefault("WWW-Authenticate", 'SSO realm="virtual-ai-factory"')
        state = scope.get("state") or {}
        method = scope.get("method")
        seclog.emit(seclog.SECURITY_LOGGER, "request.denied", logging.WARNING, reason=code, status=status,
                    method=method if method in seclog.HTTP_METHODS else "UNKNOWN", route=seclog.route_label(scope),
                    request_ref=seclog.reference("request", state.get("request_id")),
                    client_ip=state.get("client_ip") or self.client_ip(scope),
                    peer=_safe_peer(_peer(scope)), auth_mode=self.config.auth_mode,
                    actor_ref=seclog.reference("actor", principal.actor) if principal else None,
                    roles=sorted(principal.roles) if principal else None, permission=permission, **fields)
        return SecurityDenied(status, code, key, params, headers)

    def denials(self) -> dict[str, int]:
        with self._lock:
            return dict(sorted(self._denials.items()))


# ============================================================================== route dependency
def require(permission: str, *, write: bool = False) -> Callable[[Request], Access]:
    """FastAPI dependency: authenticated principal holding ``permission`` (+ write throttling)."""

    def dependency(request: Request) -> Access:
        service = request.app.state.service
        guard: Guard = service.guard
        principal = getattr(request.state, "principal", None)
        if principal is None:  # route outside /api/ (or middleware bypassed): authenticate here
            principal = guard.authenticate(request.scope)
            request.state.principal = principal
        role = guard.authorize(request.scope, principal, permission)
        access = Access(principal=principal, role=role, permission=permission,
                        request_id=getattr(request.state, "request_id", None) or uuid.uuid4().hex,
                        client_ip=getattr(request.state, "client_ip", None) or guard.client_ip(request.scope))
        try:
            scope = service.business_scope(access)
            # A default HTTP Workbench store is shared by its readers and has no per-case BU key.
            route_path = getattr(request.scope.get("route"), "path", request.url.path)
            if scope is not None and route_path.startswith("/api/v1/workbench/"):
                workbench = getattr(request.app.state, "workbench", None)
                portfolio_machine = (getattr(workbench, "mode", None) == "portfolio"
                                     and (not principal.user or principal.user in service.bu_policy.portfolio_agents))
                if not scope.all_units or portfolio_machine:
                    raise SecurityDenied(403, "business_scope_forbidden", "auth.forbidden")
                # Workbench evaluates roles again inside the SDK. Restrict that role set to
                # the grants that can address this shared workspace, not a local BU role.
                groups = service.bu_policy.groups
                roles = frozenset(granted for group, granted in guard.config.role_map
                                  if group in principal.groups and "*" in groups.get(group, ()))
                principal = replace(principal, roles=roles)
                access = replace(access, principal=principal, role=principal.role_for(permission) or role)
        except SecurityDenied as exc:
            raise guard.deny(request.scope, exc.status, exc.code, exc.key, principal=principal,
                             permission=permission) from None
        if write:
            guard.throttle(request.scope, principal)
        return access

    dependency.__factory_permission__ = permission  # type: ignore[attr-defined]
    dependency.__factory_write__ = write  # type: ignore[attr-defined]
    return dependency


# ============================================================================== middleware
class SecurityMiddleware:
    """Outermost ASGI layer: request id, client address, authentication of ``/api/*`` (deny by
    default, before the body is read) and security headers on every response."""

    def __init__(self, app: ASGIApp, guard: Guard, default_lang: str) -> None:
        self.app = app
        self.guard = guard
        self.default_lang = default_lang

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        incoming = next((v.decode("latin-1") for k, v in _headers(scope) if k == b"x-request-id"), "")
        request_id = incoming if _REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        state["request_id"] = request_id
        state["client_ip"] = self.guard.client_ip(scope)
        path = str(scope.get("path") or "")
        extra = self._response_headers(path, request_id)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {k.lower() for k, _ in headers}
                headers += [(k, v) for k, v in extra if k not in present]
                message = {**message, "headers": headers}
            await send(message)

        if path.startswith("/api/") and path not in PUBLIC_API_PATHS and scope.get("method") != "OPTIONS":
            try:
                state["principal"] = self.guard.authenticate(scope)
            except SecurityDenied as exc:
                lang = normalize_lang(_query_lang(scope), self.default_lang)
                await exc.response(lang)(scope, receive, send_with_headers)
                return
        await self.app(scope, receive, send_with_headers)

    @staticmethod
    def _response_headers(path: str, request_id: str) -> list[tuple[bytes, bytes]]:
        headers = [
            (b"x-request-id", request_id.encode("latin-1")),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
            (b"x-frame-options", b"DENY"),
            (b"cross-origin-resource-policy", b"same-origin"),
            (b"content-security-policy", API_CSP.encode("latin-1")),
        ]
        if path.startswith("/api/"):
            headers.append((b"cache-control", b"no-store"))
        return headers


def _query_lang(scope: Scope) -> str | None:
    query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
    return (query.get("lang") or [None])[0]


# ============================================================================== posture
def posture(config: SecurityConfig, audit: dict[str, Any]) -> dict[str, Any]:
    """Security posture without any secret (``/api/v1/health`` and ``meta.security``).

    ``status``: ``demo`` (authentication off), ``enforced`` (SSO identity, RBAC, four-eyes, intact
    audit chain, no configuration warning) or ``degraded`` (enforced mode with a warning, or a
    broken / unavailable audit chain). Every value is an id or a number: language-independent.
    """
    warnings = list(config.warnings)
    if not audit.get("available", True):
        warnings.append("audit_unavailable")
    elif audit.get("chain_ok") is False:
        warnings.append("audit_chain_broken")
    if not config.enforced:
        status = "demo"
    else:
        status = "degraded" if warnings else "enforced"
    return {
        "status": status,
        "auth_mode": config.auth_mode,
        "authentication": "sso_proxy" if config.enforced else "disabled",
        "rbac": "enforced" if config.enforced else "not_enforced",
        "roles": list(ROLES),
        "trusted_proxies": len(config.trusted_networks),
        "separation_of_duties": "enforced" if config.enforced else "not_enforced",
        "audit": {"enabled": True, "algorithm": "sha256-chain", **audit},
        "rate_limit": {"enabled": config.rate_limit_per_min > 0, "writes_per_minute": config.rate_limit_per_min,
                       "burst": config.rate_limit_burst},
        "body_limit_bytes": config.max_body_bytes,
        "cors": "allow_list" if config.cors_allow_list else "closed",
        "security_headers": True,
        "warnings": warnings,
    }
