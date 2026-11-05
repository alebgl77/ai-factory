"""Factory service: wires settings, configuration, state overlay, connectors, demo and engines.

Language (CONTRACT section 9): the configuration is kept as written (plain strings or ``{en, fr}``
mappings) and resolved per language by :func:`i18n.localize_tree` before the engines run; the
engines receive ``lang`` explicitly. Live telemetry is shared between localised snapshots.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar
from zoneinfo import ZoneInfo

import httpx

from .config_loader import ConfigStore
from .connectors import SOURCE_NAMES, Connectors
from .demo import generate_demo
from .engines.risk import evaluate_registry
from .i18n import LANGUAGES, localize_tree, normalize_lang
from .live import collect_live
from .security import Guard, SecurityConfig, posture
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

    def find_usecase(self, uc_id: str) -> dict[str, Any] | None:
        """Effective use case as written in Git + overlay (not localised: used for writes)."""
        for uc in self.effective_usecases():
            if str(uc.get("id")) == uc_id:
                return uc
        return None

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
                 lang: str | None = None) -> dict[str, Any]:
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
        key = (lang, days, cost_mode, today, id(cfg), self.config.generation, telemetry)
        return self._cache.get(key, lambda: self._build_snapshot(cfg, telemetry, days=days, cost_mode=cost_mode,
                                                                today=today, lang=lang), use_cache)

    def _build_snapshot(self, cfg: dict[str, Any], telemetry: _TelemetrySample | None, *, days: int,
                        cost_mode: str | None, today: dt.date, lang: str) -> dict[str, Any]:
        now = self.now(cfg)
        mode = self.settings.demo_mode
        usecases = localize_tree(self.effective_usecases(cfg), lang)
        lcfg = self.localized_config(lang, cfg)
        demo = (generate_demo(lcfg, self.settings.demo_seed, today, days=max(30, days), lang=lang)
                if mode in ("auto", "always") else None)
        sources = telemetry.sources if telemetry else {}
        live = telemetry.data if telemetry else {}
        snap = assemble_snapshot(lcfg, usecases, demo, live, demo_mode=mode, seed=self.settings.demo_seed,
                                 today=today, now=now, days=days, cost_mode=cost_mode, sources=sources,
                                 lang=lang, languages=LANGUAGES)
        if telemetry and telemetry.collected_at:
            snap["meta"]["telemetry_collected_at"] = telemetry.collected_at
        snap["meta"]["security"] = self.security_posture()  # trust badge of the dashboard
        return snap
