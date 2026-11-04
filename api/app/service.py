"""Factory service: wires settings, configuration, state overlay, connectors, demo and engines.

Language (CONTRACT section 9): the configuration is kept as written (plain strings or ``{en, fr}``
mappings) and resolved per language by :func:`i18n.localize_tree` before the engines run; the
engines receive ``lang`` explicitly. Snapshots are cached per language.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from .config_loader import ConfigStore
from .connectors import SOURCE_NAMES, Connectors
from .demo import generate_demo
from .engines.risk import evaluate_registry
from .i18n import LANGUAGES, localize_tree, normalize_lang
from .live import collect_live
from .settings import Settings
from .snapshot import assemble_snapshot
from .state import StateStore, apply_registry_overrides
from .util import to_date


class FactoryService:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], dt.datetime] | None = None) -> None:
        self.settings = settings
        self.config = ConfigStore(settings.config_dir)
        self.state = StateStore(settings.state_dir)
        self.connectors = Connectors(settings, transport=transport)
        self._clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self._lock = threading.Lock()
        self._cache: dict[Any, tuple[float, dict[str, Any]]] = {}
        self._epoch = 0

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

    def sources(self, force: bool = False) -> dict[str, bool]:
        if self.settings.demo_mode == "always":
            return {name: False for name in SOURCE_NAMES}
        return self.connectors.health(force=force)

    def invalidate(self) -> None:
        with self._lock:
            self._epoch += 1
            self._cache.clear()

    def snapshot(self, *, days: int = 30, cost_mode: str | None = None, use_cache: bool = True,
                 lang: str | None = None) -> dict[str, Any]:
        """Fully resolved snapshot in ``lang`` (default ``FACTORY_DEFAULT_LANG``), cached per language."""
        lang = self.lang(lang)
        cfg = self.config.get()
        today = self.today(cfg)
        key = (lang, days, cost_mode, today.isoformat(), self.config.generation, self._epoch)
        if use_cache and self.settings.cache_ttl_s > 0:
            with self._lock:
                hit = self._cache.get(key)
                if hit and time.monotonic() - hit[0] < self.settings.cache_ttl_s:
                    return hit[1]
        snap = self.build(cfg, days=days, cost_mode=cost_mode, today=today, lang=lang)
        if self.settings.cache_ttl_s > 0:
            with self._lock:
                self._cache = {k: v for k, v in self._cache.items() if time.monotonic() - v[0] < self.settings.cache_ttl_s}
                self._cache[key] = (time.monotonic(), snap)
        return snap

    def build(self, cfg: dict[str, Any], *, days: int = 30, cost_mode: str | None = None,
              today: dt.date | None = None, lang: str | None = None) -> dict[str, Any]:
        lang = self.lang(lang)
        today = today or self.today(cfg)
        now = self.now(cfg)
        mode = self.settings.demo_mode
        usecases = localize_tree(self.effective_usecases(cfg), lang)
        lcfg = self.localized_config(lang, cfg)
        demo = (generate_demo(lcfg, self.settings.demo_seed, today, days=max(30, days), lang=lang)
                if mode in ("auto", "always") else None)
        sources = self.sources()
        live: dict[str, Any] = {}
        if mode in ("auto", "never") and any(sources.values()):
            live = collect_live(self.connectors, sources, today=today, now=now, days=days, tz=self.tz(cfg))
        return assemble_snapshot(lcfg, usecases, demo, live, demo_mode=mode, seed=self.settings.demo_seed,
                                 today=today, now=now, days=days, cost_mode=cost_mode, sources=sources,
                                 lang=lang, languages=LANGUAGES)
