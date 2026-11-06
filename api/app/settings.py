"""Runtime settings, read from environment variables (CONTRACT section 3).

Secrets (Langfuse secret key, LiteLLM master key, Grafana token file content) are kept on
this object only. The object is never serialised into a response.

Security settings (see ``app/security.py`` and the API README): ``FACTORY_AUTH_MODE``,
``FACTORY_TRUSTED_PROXIES``, ``FACTORY_ROLE_MAP``, ``FACTORY_DEFAULT_ROLE``,
``FACTORY_RATE_LIMIT_PER_MIN`` and ``FACTORY_RATE_LIMIT_BURST``. They are parsed and validated
by :class:`app.security.SecurityConfig`; invalid values fail closed and are reported as warnings.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

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


def _env_list(name: str) -> tuple[str, ...]:
    """Comma-separated list (blank items dropped)."""
    return tuple(item.strip() for item in (_env(name, "") or "").split(",") if item.strip())


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
    shadow_ai_enabled: bool = field(default=False, kw_only=True)
    shadow_ai_groups: tuple[str, ...] = field(default=(), kw_only=True)
    shadow_ai_privacy_threshold: int = field(default=5, kw_only=True)
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
    # -- security (app/security.py) -------------------------------------------------------------
    auth_mode: str = "none"                  # FACTORY_AUTH_MODE: none (demo) | proxy (SSO in front)
    trusted_proxies: tuple[str, ...] = ()    # FACTORY_TRUSTED_PROXIES: CIDRs allowed to assert identity
    role_map: tuple[str, ...] = ()           # FACTORY_ROLE_MAP: "group:role" items
    default_role: str | None = None          # FACTORY_DEFAULT_ROLE: role of an authenticated user without mapped group
    rate_limit_per_min: int = 60             # FACTORY_RATE_LIMIT_PER_MIN: write requests per actor/IP (0 = off)
    rate_limit_burst: int = 20               # FACTORY_RATE_LIMIT_BURST: bucket size
    cost_fx: float = 1.0                     # FACTORY_COST_FX: native gateway unit to EUR
    workbench_mode: str = "off"              # off | demo | protected_demo
    workbench_chat_mode: str = "simulation"  # simulation | local
    workbench_cyber_audit: str = "off"        # off | on

    def __post_init__(self) -> None:
        object.__setattr__(self, "default_lang", normalize_lang(self.default_lang, DEFAULT_LANG))
        if not math.isfinite(self.cost_fx) or self.cost_fx <= 0:
            raise ValueError("FACTORY_COST_FX must be a finite positive rate")
        if self.shadow_ai_enabled and (self.auth_mode != "proxy" or not self.shadow_ai_groups):
            raise ValueError("Shadow AI requires proxy authentication and FACTORY_SHADOW_AI_GROUPS")
        if type(self.shadow_ai_privacy_threshold) is not int or not 5 <= self.shadow_ai_privacy_threshold <= 100:
            raise ValueError("FACTORY_SHADOW_AI_PRIVACY_THRESHOLD must be between 5 and 100")
        if self.workbench_mode not in {"off", "demo", "protected_demo"}:
            raise ValueError("invalid FACTORY_WORKBENCH_MODE")
        if self.workbench_chat_mode not in {"simulation", "local"}:
            raise ValueError("invalid FACTORY_WORKBENCH_CHAT_MODE")
        if self.workbench_cyber_audit not in {"off", "on"}:
            raise ValueError("invalid FACTORY_WORKBENCH_CYBER_AUDIT")
        if self.workbench_chat_mode == "local" and (
            self.workbench_mode != "protected_demo" or self.auth_mode != "proxy"
            or not self.litellm_url or not self.litellm_master_key
        ):
            raise ValueError("local workbench chat requires protected_demo, proxy auth and LiteLLM")
        if self.workbench_chat_mode == "local":
            try:
                gateway = urlsplit(self.litellm_url or "")
                valid = (
                    gateway.scheme in {"http", "https"}
                    and gateway.hostname in {"litellm", "localhost", "127.0.0.1", "::1"}
                    and gateway.username is None and not gateway.path.rstrip("/")
                    and not gateway.query and not gateway.fragment
                    and (gateway.port is None or gateway.port > 0)
                )
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("local workbench chat requires the internal litellm or loopback gateway URL")

    @classmethod
    def from_env(cls) -> "Settings":
        mode = (_env("FACTORY_DEMO_MODE", "auto") or "auto").lower()
        if mode not in DEMO_MODES:
            mode = "auto"
        cors = tuple(o.strip() for o in (_env("FACTORY_CORS_ORIGINS", "") or "").split(",") if o.strip())
        try:
            cost_fx = float(_env("FACTORY_COST_FX", "1.0") or "1.0")
        except ValueError:
            raise ValueError("FACTORY_COST_FX must be a finite positive rate") from None
        shadow_ai = (_env("FACTORY_SHADOW_AI_ENABLED", "off") or "off").lower()
        if shadow_ai not in {"on", "off"}:
            raise ValueError("invalid FACTORY_SHADOW_AI_ENABLED")
        try:
            shadow_threshold = int(_env("FACTORY_SHADOW_AI_PRIVACY_THRESHOLD", "5") or "5")
        except ValueError:
            raise ValueError("FACTORY_SHADOW_AI_PRIVACY_THRESHOLD must be between 5 and 100") from None
        return cls(
            config_dir=_env("FACTORY_CONFIG_DIR", "/config") or "/config",
            demo_mode=mode,
            demo_seed=_env_int("FACTORY_DEMO_SEED", 42),
            demo_date=_env("FACTORY_DEMO_DATE"),
            state_dir=_env("FACTORY_STATE_DIR", "/data") or "/data",
            shadow_ai_enabled=shadow_ai == "on",
            shadow_ai_groups=_env_list("FACTORY_SHADOW_AI_GROUPS"),
            shadow_ai_privacy_threshold=shadow_threshold,
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
            auth_mode=(_env("FACTORY_AUTH_MODE", "none") or "none").lower(),
            trusted_proxies=_env_list("FACTORY_TRUSTED_PROXIES"),
            role_map=_env_list("FACTORY_ROLE_MAP"),
            default_role=(_env("FACTORY_DEFAULT_ROLE") or "").lower() or None,
            rate_limit_per_min=max(0, _env_int("FACTORY_RATE_LIMIT_PER_MIN", 60)),
            rate_limit_burst=max(1, _env_int("FACTORY_RATE_LIMIT_BURST", 20)),
            cost_fx=cost_fx,
            workbench_mode=(_env("FACTORY_WORKBENCH_MODE", "off") or "off").lower(),
            workbench_chat_mode=(_env("FACTORY_WORKBENCH_CHAT_MODE", "simulation") or "simulation").lower(),
            workbench_cyber_audit=os.environ.get("FACTORY_WORKBENCH_CYBER_AUDIT", "off"),
        )
