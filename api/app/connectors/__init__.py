"""Connector hub: builds the clients from the settings and probes them in parallel."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from ..settings import Settings
from .base import SourceError
from .grafana import GrafanaClient, NAMED_QUERIES, PathNotAllowed
from .langfuse import LangfuseClient
from .litellm import LiteLLMClient
from .prometheus import PrometheusClient
from .qdrant import QdrantClient

SOURCE_NAMES = ("prometheus", "grafana", "langfuse", "litellm", "qdrant")

__all__ = ["Connectors", "SOURCE_NAMES", "SourceError", "PathNotAllowed", "NAMED_QUERIES"]


class Connectors:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None,
                 health_ttl_s: float = 15.0) -> None:
        kw = {"timeout": settings.connector_timeout_s, "transport": transport}
        self.prometheus = PrometheusClient(settings.prometheus_url, **kw)
        self.grafana = GrafanaClient(settings.grafana_url, settings.grafana_token_file, settings.grafana_prom_ds_uid,
                                     settings.grafana_public_url, **kw)
        self.langfuse = LangfuseClient(settings.langfuse_host, settings.langfuse_public_key,
                                       settings.langfuse_secret_key, **kw)
        self.litellm = LiteLLMClient(settings.litellm_url, settings.litellm_master_key, **kw)
        self.qdrant = QdrantClient(settings.qdrant_url, **kw)
        self._ttl = health_ttl_s
        self._lock = threading.Lock()
        self._cached: tuple[float, dict[str, bool]] | None = None

    def clients(self) -> dict[str, Any]:
        return {"prometheus": self.prometheus, "grafana": self.grafana, "langfuse": self.langfuse,
                "litellm": self.litellm, "qdrant": self.qdrant}

    def any_configured(self) -> bool:
        return any(c.configured for c in self.clients().values())

    def health(self, force: bool = False) -> dict[str, bool]:
        with self._lock:
            if not force and self._cached and time.monotonic() - self._cached[0] < self._ttl:
                return dict(self._cached[1])
        clients = self.clients()

        def probe(name: str) -> bool:
            client = clients[name]
            if not client.configured:
                return False
            try:
                return bool(client.health())
            except Exception:  # a connector must never crash the API
                return False

        with ThreadPoolExecutor(max_workers=len(SOURCE_NAMES)) as pool:
            results = dict(zip(SOURCE_NAMES, pool.map(probe, SOURCE_NAMES), strict=True))
        with self._lock:
            self._cached = (time.monotonic(), results)
        return dict(results)

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None
