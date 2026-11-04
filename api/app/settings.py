"""Runtime settings, read from environment variables (CONTRACT section 3).

Secrets (Langfuse secret key, LiteLLM master key, Grafana token file content) are kept on
this object only. The object is never serialised into a response.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .i18n import DEFAULT_LANG, normalize_lang

DEMO_MODES = ("auto", "always", "never")
API_VERSION = "1.0.0"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    config_dir: str = "/config"
    demo_mode: str = "auto"
    demo_seed: int = 42
    demo_date: str | None = None  # optional ISO anchor date override (FACTORY_DEMO_DATE)
    state_dir: str = "/data"
    prometheus_url: str | None = None
    grafana_url: str | None = None
    grafana_token_file: str | None = None
    grafana_prom_ds_uid: str = "prometheus"
    grafana_public_url: str = "/grafana"
    langfuse_host: str | None = None
    langfuse_public_key: str | None = field(default=None, repr=False)
    langfuse_secret_key: str | None = field(default=None, repr=False)
    litellm_url: str | None = None
    litellm_master_key: str | None = field(default=None, repr=False)
    qdrant_url: str | None = None
    cors_origins: tuple[str, ...] = ()
    max_body_bytes: int = 64 * 1024
    connector_timeout_s: float = 2.0
    cache_ttl_s: float = 10.0
    default_lang: str = DEFAULT_LANG  # FACTORY_DEFAULT_LANG: en | fr (CONTRACT section 9)

    def __post_init__(self) -> None:
        object.__setattr__(self, "default_lang", normalize_lang(self.default_lang, DEFAULT_LANG))

    @classmethod
    def from_env(cls) -> "Settings":
        mode = (_env("FACTORY_DEMO_MODE", "auto") or "auto").lower()
        if mode not in DEMO_MODES:
            mode = "auto"
        cors = tuple(o.strip() for o in (_env("FACTORY_CORS_ORIGINS", "") or "").split(",") if o.strip())
        return cls(
            config_dir=_env("FACTORY_CONFIG_DIR", "/config") or "/config",
            demo_mode=mode,
            demo_seed=_env_int("FACTORY_DEMO_SEED", 42),
            demo_date=_env("FACTORY_DEMO_DATE"),
            state_dir=_env("FACTORY_STATE_DIR", "/data") or "/data",
            prometheus_url=_env("PROMETHEUS_URL"),
            grafana_url=_env("GRAFANA_URL"),
            grafana_token_file=_env("GRAFANA_TOKEN_FILE"),
            grafana_prom_ds_uid=_env("GRAFANA_PROM_DS_UID", "prometheus") or "prometheus",
            grafana_public_url=_env("GRAFANA_PUBLIC_URL", "/grafana") or "/grafana",
            langfuse_host=_env("LANGFUSE_HOST"),
            langfuse_public_key=_env("LANGFUSE_PUBLIC_KEY"),
            langfuse_secret_key=_env("LANGFUSE_SECRET_KEY"),
            litellm_url=_env("LITELLM_URL"),
            litellm_master_key=_env("LITELLM_MASTER_KEY"),
            qdrant_url=_env("QDRANT_URL"),
            cors_origins=cors,
            max_body_bytes=max(1024, _env_int("FACTORY_MAX_BODY_BYTES", 64 * 1024)),
            connector_timeout_s=min(10.0, max(0.2, _env_float("FACTORY_CONNECTOR_TIMEOUT_S", 2.0))),
            cache_ttl_s=max(0.0, _env_float("FACTORY_CACHE_TTL_S", 10.0)),
            default_lang=normalize_lang(_env("FACTORY_DEFAULT_LANG"), DEFAULT_LANG),
        )
