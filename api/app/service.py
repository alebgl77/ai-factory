"""Factory service: wires settings, configuration, state overlay, connectors, demo and engines.

Language (CONTRACT section 9): the configuration is kept as written (plain strings or ``{en, fr}``
mappings) and resolved per language by :func:`i18n.localize_tree` before the engines run; the
engines receive ``lang`` explicitly. Live telemetry is shared between localised snapshots.
"""

from __future__ import annotations

import datetime as dt
import hmac
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar
from zoneinfo import ZoneInfo

import httpx

from .audit import canonical
from .bu_access import BUScope, BusinessPolicy
from .config_loader import ConfigStore
from .connectors import SOURCE_NAMES, Connectors
from .demo import generate_demo
from .engines.risk import evaluate_registry
from .i18n import LANGUAGES, localize_tree, normalize_lang
from .live import collect_live
from .security import Access, Guard, Principal, SecurityConfig, SecurityDenied, posture
from .settings import Settings
from .snapshot import assemble_snapshot
from .state import StateStore, apply_registry_overrides
from .util import to_date

_T = TypeVar("_T")


class _SnapshotCache(Generic[_T]):
    """Cache completed values for a TTL and share an in-flight build per key."""

    def __init__(self, ttl: float) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._entries: dict[tuple[Any, ...], tuple[float, Future[_T]]] = {}

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def get(self, key: tuple[Any, ...], build: Callable[[], _T], use_cache: bool) -> _T:
        if not use_cache or self._ttl <= 0:
            return build()
        with self._lock:
            now = time.monotonic()
            self._entries = {k: entry for k, entry in self._entries.items() if entry[0] > now}
            hit = self._entries.get(key)
            future = hit[1] if hit is not None else Future()
            if hit is None:
                self._entries[key] = (float("inf"), future)
        if hit is not None:
            return future.result()
        try:
            value = build()
        except BaseException as exc:
            with self._lock:
                if self._owns(key, future):
                    del self._entries[key]
            future.set_exception(exc)
            raise
        with self._lock:
            if self._owns(key, future):
                self._entries[key] = (time.monotonic() + self._ttl, future)
        future.set_result(value)
        return value

    def _owns(self, key: tuple[Any, ...], future: Future[_T]) -> bool:
        entry = self._entries.get(key)
        return entry is not None and entry[1] is future


@dataclass(frozen=True, eq=False)
class _TelemetrySample:
    sources: dict[str, bool]
    data: dict[str, Any]
    collected_at: str | None


class FactoryService:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], dt.datetime] | None = None) -> None:
        self.settings = settings
        self.config = ConfigStore(settings.config_dir)
        self.state = StateStore(settings.state_dir)
        self.connectors = Connectors(settings, transport=transport)
        self.guard = Guard(SecurityConfig.from_settings(settings))
        self.bu_policy = (BusinessPolicy.load(settings.bu_policy_path, self.config.get(), self.guard.config)
                          if settings.bu_policy_path else None)
        self.booking_key = self.state.booking_key() if self.bu_policy else None
        self.operating_evidence = None
        self.catalogue_observatory = None
        self._clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self._cache = _SnapshotCache[dict[str, Any]](settings.cache_ttl_s)
        self._telemetry = _SnapshotCache[_TelemetrySample](settings.cache_ttl_s)

    # ------------------------------------------------------------------ language
    def lang(self, value: Any = None) -> str:
        """Requested language, else ``FACTORY_DEFAULT_LANG`` (unknown values fall back to it)."""
        return normalize_lang(value, self.settings.default_lang)

    def localized_config(self, lang: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        """Configuration with every ``{en, fr}`` mapping resolved for ``lang`` (input never mutated)."""
        return localize_tree(cfg if cfg is not None else self.config.get(), self.lang(lang))

    # ------------------------------------------------------------------ time
    def tz(self, cfg: dict[str, Any] | None = None) -> dt.tzinfo:
        cfg = cfg or self.config.get()
        name = ((cfg.get("factory") or {}).get("factory") or {}).get("timezone") or "UTC"
        try:
            return ZoneInfo(str(name))
        except Exception:
            return dt.timezone.utc

    def now(self, cfg: dict[str, Any] | None = None) -> dt.datetime:
        return self._clock().astimezone(self.tz(cfg))

    def today(self, cfg: dict[str, Any] | None = None) -> dt.date:
        override = to_date(self.settings.demo_date)
        return override or self.now(cfg).date()

    # ------------------------------------------------------------------ data
    def effective_usecases(self, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        cfg = cfg or self.config.get()
        return apply_registry_overrides(cfg.get("usecases") or [], self.state.registry_overrides())

    def find_configured_usecase(self, uc_id: str) -> dict[str, Any] | None:
        """Git baseline for a write; the state store applies the current overlay under lock."""
        for uc in self.config.get().get("usecases") or []:
            if str(uc.get("id")) == uc_id:
                return uc
        return None

    def business_scope(self, access: Access, groups: frozenset[str] | None = None, *,
                       permission: str | None = None) -> BUScope | None:
        if self.bu_policy is None:
            return None
        scope = self.bu_policy.scope(access, self.guard.config, groups=groups, permission=permission)
        if scope is None:
            raise SecurityDenied(403, "business_scope_forbidden", "auth.forbidden")
        return scope

    def scope_case_ids(self, scope: BUScope | None) -> frozenset[str]:
        return frozenset(str(case["id"]) for case in self.config.get().get("usecases") or []
                         if scope is None or scope.allows(case.get("cost_center")))

    def scoped_case(self, uc_id: str, access: Access) -> tuple[dict[str, Any] | None, Access]:
        scope = self.business_scope(access)
        case = self.find_configured_usecase(uc_id)
        if case is None or (scope is not None and not scope.allows(case.get("cost_center"))):
            return None, access
        if self.bu_policy is not None:
            access = self.bu_policy.scoped_role(access, self.guard.config, case.get("cost_center"))
        return case, access

    def reviewer_subject_ref(self, principal: Principal) -> str | None:
        if self.booking_key is None or not principal.authenticated or not principal.user:
            return None
        return hmac.digest(self.booking_key, b"factory-bu-subject-v1\0" + principal.user.encode(), "sha256").hex()

    def evaluate_usecase(self, uc: dict[str, Any], lang: str) -> dict[str, Any]:
        """Risk evaluation of one effective use case, resolved for ``lang``."""
        lang = self.lang(lang)
        cfg = self.config.get()
        lcfg = self.localized_config(lang, cfg)
        people = (lcfg.get("profiles") or {}).get("people") or []
        item = evaluate_registry([localize_tree(uc, lang)], lcfg.get("factory") or {}, lcfg.get("controls") or [],
                                 people, self.today(cfg), lang)[0]
        return localize_tree(item, lang)

    def security_posture(self) -> dict[str, Any]:
        """Security posture (no secret, language-independent): ``/health`` and ``meta.security``."""
        return posture(self.guard.config, self.state.audit_status())

    def sources(self, force: bool = False) -> dict[str, bool]:
        if self.settings.demo_mode == "always":
            return {name: False for name in SOURCE_NAMES}
        return self.connectors.health(force=force)

    def invalidate(self) -> None:
        # Local writes affect assembly, not observations pulled from external sources.
        self._cache.clear()

    def _collect_telemetry(self, today: dt.date, days: int, tz: dt.tzinfo) -> _TelemetrySample:
        sources = self.sources()
        now = self._clock().astimezone(tz)
        live = (collect_live(self.connectors, sources, today=today, now=now, days=days, tz=tz,
                             cost_fx=self.settings.cost_fx) if any(sources.values()) else {})
        return _TelemetrySample(sources, live, now.replace(microsecond=0).isoformat() if live else None)

    def snapshot(self, *, days: int = 30, cost_mode: str | None = None, use_cache: bool = True,
                 lang: str | None = None, scope: BUScope | None = None) -> dict[str, Any]:
        """Fully resolved snapshot in ``lang`` (default ``FACTORY_DEFAULT_LANG``), cached per language."""
        lang = self.lang(lang)
        cfg = self.config.get()
        today = self.today(cfg)
        telemetry = None
        if self.settings.demo_mode != "always" and self.connectors.any_configured():
            tz = self.tz(cfg)
            telemetry = self._telemetry.get((today, days, tz), lambda: self._collect_telemetry(today, days, tz),
                                            use_cache)
        # The sample identity ensures no rendered snapshot outlives its raw telemetry cache entry.
        key = (lang, days, cost_mode, today, id(cfg), self.config.generation, telemetry, scope)
        result = self._cache.get(key, lambda: self._build_snapshot(cfg, telemetry, days=days, cost_mode=cost_mode,
                                                                 today=today, lang=lang, scope=scope), use_cache)
        return self._project_operations(result, cfg, scope, lang)

    def _project_operations(self, snapshot: dict[str, Any], cfg: dict[str, Any], scope: BUScope | None,
                            lang: str) -> dict[str, Any]:
        if self.operating_evidence is not None and scope is not None:
            from .operating_evidence import project_operations

            snapshot = project_operations(snapshot, self.operating_evidence, cfg.get("factory") or {}, lang=lang)
        if self.catalogue_observatory is not None:
            from .operating_evidence import project_catalogue

            snapshot = {**snapshot, "catalog": project_catalogue(snapshot["catalog"], self.catalogue_observatory)}
        return snapshot

    def _build_snapshot(self, cfg: dict[str, Any], telemetry: _TelemetrySample | None, *, days: int,
                        cost_mode: str | None, today: dt.date, lang: str, scope: BUScope | None) -> dict[str, Any]:
        now = self.now(cfg)
        mode = self.settings.demo_mode if scope is None else "never"
        usecases = localize_tree(self.effective_usecases(cfg), lang)
        lcfg = self.localized_config(lang, cfg)
        demo = (generate_demo(lcfg, self.settings.demo_seed, today, days=max(30, days), lang=lang)
                if mode in ("auto", "always") else None)
        sources = telemetry.sources if telemetry else {}
        live = telemetry.data if telemetry else {}
        partial = scope is not None and not scope.all_units
        if partial:
            usecases = [case for case in usecases if scope.allows(case.get("cost_center"))]
            ids = {case["id"] for case in usecases}
            lcfg = self._scope_config(lcfg, usecases, scope)
            usage = [row for row in live.get("usage") or [] if row.get("usecase") in ids]
            live = {"usage": usage, "usage_origin": live.get("usage_origin")} if usage else {}
            sources = {}
            cost_mode = "showback"  # shared fixed charges cannot be reallocated from a partial denominator
        snap = assemble_snapshot(lcfg, usecases, demo, live, demo_mode=mode, seed=self.settings.demo_seed,
                                 today=today, now=now, days=days, cost_mode=cost_mode, sources=sources,
                                 lang=lang, languages=LANGUAGES)
        if telemetry and telemetry.collected_at and (not partial or live):
            snap["meta"]["telemetry_collected_at"] = telemetry.collected_at
        if scope is not None:
            snap["meta"]["business_scope"] = True
            assert self.bu_policy is not None and self.booking_key is not None
            view = ["factory-dashboard-view-v1", self.bu_policy.fingerprint, scope.all_units, sorted(scope.centers),
                    sorted(case["id"] for case in usecases)]
            snap["meta"]["business_view"] = hmac.digest(self.booking_key, canonical(view), "sha256").hex()
            snap["grafana"]["dashboards"] = [{key: dashboard.get(key) for key in ("uid", "title")}
                                             for dashboard in snap["grafana"]["dashboards"]]
        if partial:
            snap["costs"]["scope"] = "attributed_only"
            for case in snap["registry"]:
                case["assessment"].pop("reviewed_by", None)
        snap["meta"]["security"] = (posture(self.guard.config, {}) if partial else self.security_posture())
        return snap

    def _scope_config(self, cfg: dict[str, Any], cases: list[dict[str, Any]], scope: BUScope) -> dict[str, Any]:
        assert self.bu_policy is not None
        ids = {case["id"] for case in cases}
        people = self.bu_policy.members(scope)
        profiles = cfg.get("profiles") or {}
        catalogue = cfg.get("catalog") or {}
        rates = cfg["factory"].get("rates") or {}
        agents = [agent for agent in catalogue.get("agents") or [] if agent.get("usecase") in ids]
        tools = {tool for agent in agents for tool in agent.get("tools") or []}
        models = {model for case in cases for model in ((case.get("model") or {}).get("alias"),
                                                         (case.get("rag") or {}).get("embedding"),
                                                         (case.get("eval") or {}).get("judge_alias")) if model}
        return {**cfg, "warnings": [], "skills": {},
                "factory": {**cfg["factory"], "cost_centers": [center for center in cfg["factory"].get("cost_centers") or []
                                                               if center.get("id") in scope.centers],
                            "rates": {**rates, "local_gpu": {**(rates.get("local_gpu") or {}), "capex_eur": 0},
                                      "vps_eur_per_month": 0, "platform_overhead_pct": 0}},
                "profiles": {**profiles, "people": [person for person in profiles.get("people") or []
                                                    if person.get("id") in people]},
                "catalog": {"agents": agents, "tools": [tool for tool in catalogue.get("tools") or []
                                                         if tool.get("id") in tools],
                            "models": [model for model in catalogue.get("models") or [] if model.get("alias") in models]}}
