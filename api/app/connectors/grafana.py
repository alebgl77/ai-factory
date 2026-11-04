"""Server-side Grafana client (Grafana runs as a separate, unmodified AGPLv3 service).

Security rules (CONTRACT section 3):
- only GET requests, only to an allow-list of Grafana HTTP API paths;
- the Viewer service-account token is read from GRAFANA_TOKEN_FILE at call time, sent in the
  Authorization header and never returned, logged or echoed (responses are re-built from
  selected fields, and any occurrence of the token is redacted as defence in depth);
- PromQL is never taken from the client: only named queries of NAMED_QUERIES run.
"""

from __future__ import annotations

import re
from typing import Any

from ..i18n import DEFAULT_LANG, has, msg
from .base import HttpSource, SourceError
from .prometheus import parse_vector

_UID = r"[A-Za-z0-9_-]{1,64}"
ALLOWED_PATHS = (
    re.compile(r"^/api/search$"),
    re.compile(rf"^/api/dashboards/uid/{_UID}$"),
    re.compile(rf"^/api/datasources/proxy/uid/{_UID}/api/v1/query$"),
)
UID_RE = re.compile(rf"^{_UID}$")
REDACTED = "[redacted]"
NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")

# Named, allow-listed PromQL queries (the only ones the proxy can run). Labels and the non-symbol
# units ("alerts", "usecases") are localised through the message catalogue (grafana.query.<name>,
# grafana.unit.<unit>); the query strings and names never change.
NAMED_QUERIES: dict[str, dict[str, str]] = {
    "gpu_util": {"query": "avg by (gpu) (DCGM_FI_DEV_GPU_UTIL)", "unit": "%"},
    "gpu_temp": {"query": "max by (gpu) (DCGM_FI_DEV_GPU_TEMP)", "unit": "°C"},
    "gpu_power": {"query": "sum by (gpu) (DCGM_FI_DEV_POWER_USAGE)", "unit": "W"},
    "gpu_mem_used": {"query": "sum by (gpu) (DCGM_FI_DEV_FB_USED)", "unit": "MiB"},
    "llm_requests_rate": {"query": "sum by (usecase) (rate(factory_llm_requests_total[5m]))", "unit": "req/s"},
    "llm_error_ratio": {"query": "sum(rate(factory_llm_requests_total{status!=\"success\"}[5m])) / "
                                 "clamp_min(sum(rate(factory_llm_requests_total[5m])), 1e-9)",
                        "unit": "ratio"},
    "llm_cost_24h": {"query": "sum by (usecase) (increase(factory_llm_cost_eur_total[24h]))", "unit": "EUR"},
    "llm_tokens_24h": {"query": "sum by (usecase, direction) (increase(factory_llm_tokens_total[24h]))",
                       "unit": "tokens"},
    "llm_ttft_p95": {"query": "histogram_quantile(0.95, sum by (le) (rate(factory_llm_ttft_seconds_bucket[5m])))",
                     "unit": "s"},
    "llm_latency_p95": {"query": "histogram_quantile(0.95, sum by (le) (rate(factory_llm_latency_seconds_bucket[5m])))",
                        "unit": "s"},
    "waste_alerts": {"query": "sum by (rule, severity) (factory_waste_alerts)", "unit": "alerts"},
    "reevaluation_required": {"query": "sum(factory_reevaluation_required)", "unit": "usecases"},
    "skills_coverage": {"query": "factory_skills_coverage_ratio", "unit": "ratio"},
    "targets_up": {"query": "up", "unit": "bool"},
}


def query_label(name: str, lang: str = DEFAULT_LANG) -> str:
    return msg(lang, f"grafana.query.{name}")


def query_unit(name: str, lang: str = DEFAULT_LANG) -> str:
    unit = NAMED_QUERIES[name]["unit"]
    return msg(lang, f"grafana.unit.{unit}") if has(f"grafana.unit.{unit}") else unit


def describe_queries(lang: str = DEFAULT_LANG) -> list[dict[str, str]]:
    return [{"name": k, "label": query_label(k, lang), "unit": query_unit(k, lang)} for k in sorted(NAMED_QUERIES)]


class PathNotAllowed(SourceError):
    pass


def is_allowed(path: str) -> bool:
    return any(p.match(path) for p in ALLOWED_PATHS)


def _redact(obj: Any, secret: str | None) -> Any:
    if not secret:
        return obj
    if isinstance(obj, dict):
        return {k: _redact(v, secret) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v, secret) for v in obj]
    if isinstance(obj, str) and secret in obj:
        return obj.replace(secret, REDACTED)
    return obj


class GrafanaClient(HttpSource):
    name = "grafana"

    def __init__(self, base_url: str | None, token_file: str | None, ds_uid: str, public_url: str = "/grafana",
                 **kwargs: Any) -> None:
        super().__init__(base_url, **kwargs)
        self.token_file = token_file
        self.ds_uid = ds_uid if UID_RE.match(ds_uid or "") else "prometheus"
        self.public_url = (public_url or "").rstrip("/")

    def _token(self) -> str | None:
        if not self.token_file:
            return None
        try:
            with open(self.token_file, "r", encoding="utf-8") as fh:
                token = fh.read(8192).strip()
        except OSError:
            return None
        if not token or len(token) > 4096 or any(c.isspace() for c in token) or not token.isprintable():
            return None
        return token

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token_file)

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET an allow-listed Grafana path with the server-side token (only method exposed)."""
        if not is_allowed(path):
            raise PathNotAllowed("grafana: path not allowed")
        token = self._token()
        if token is None:
            raise SourceError("grafana: token unavailable")
        payload = self._request_json(path, params=params, headers={"Authorization": f"Bearer {token}",
                                                                    "Accept": "application/json"})
        return _redact(payload, token)

    def health(self) -> bool:
        if not self.configured:
            return False
        try:
            self.get("/api/search", {"limit": 1})
            return True
        except SourceError:
            return False

    def _public(self, url: str) -> str:
        url = url if url.startswith("/") else "/" + url
        if not self.public_url:
            return url
        if self.public_url.startswith(("http://", "https://")):
            return self.public_url + url
        if url.startswith(self.public_url + "/"):
            return url
        return self.public_url + url

    def dashboards(self) -> list[dict[str, Any]]:
        payload = self.get("/api/search", {"type": "dash-db", "limit": 200})
        if not isinstance(payload, list):
            raise SourceError("grafana: unexpected answer")
        out = []
        for item in payload:
            if not isinstance(item, dict) or not UID_RE.match(str(item.get("uid", ""))):
                continue
            out.append({
                "uid": item.get("uid"),
                "title": item.get("title"),
                "url": self._public(str(item.get("url") or f"/d/{item.get('uid')}")),
                "tags": [str(t) for t in item.get("tags") or []],
                "folder": item.get("folderTitle"),
            })
        out.sort(key=lambda d: str(d["title"]))
        return out

    def dashboard(self, uid: str) -> dict[str, Any]:
        if not UID_RE.match(uid or ""):
            raise PathNotAllowed("grafana: invalid uid")
        payload = self.get(f"/api/dashboards/uid/{uid}")
        dash = (payload or {}).get("dashboard") or {}
        meta = (payload or {}).get("meta") or {}
        panels = []
        for p in dash.get("panels") or []:
            if isinstance(p, dict):
                panels.append({"id": p.get("id"), "title": p.get("title"), "type": p.get("type")})
        return {"uid": dash.get("uid"), "title": dash.get("title"), "tags": dash.get("tags") or [],
                "version": dash.get("version"), "panels": panels,
                "url": self._public(str(meta.get("url") or f"/d/{uid}"))}

    def metric(self, name: str, lang: str = DEFAULT_LANG) -> dict[str, Any]:
        if not NAME_RE.match(name or "") or name not in NAMED_QUERIES:
            raise KeyError(name)
        spec = NAMED_QUERIES[name]
        payload = self.get(f"/api/datasources/proxy/uid/{self.ds_uid}/api/v1/query", {"query": spec["query"]})
        return {"name": name, "label": query_label(name, lang), "unit": query_unit(name, lang),
                "query": spec["query"], "result": parse_vector(payload), "source": "grafana"}
