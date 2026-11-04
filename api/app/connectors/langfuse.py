"""Langfuse public API connector (MIT core). Basic auth with the public/secret key pair.

Agent Ops samples are built per trace (one trace = one agent task):
- TTFT = completionStartTime - startTime of the first GENERATION observation;
- tokens per task = sum of the generation usage;
- vector latency = duration of retrieval spans (name containing retriev/vector/qdrant).
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

from ..util import UNATTRIBUTED, to_datetime
from .base import HttpSource, SourceError

_VECTOR_HINTS = ("retriev", "vector", "qdrant", "search")


def _ms(start: Any, end: Any) -> float | None:
    a, b = to_datetime(start), to_datetime(end)
    if a is None or b is None:
        return None
    return max(0.0, (b - a).total_seconds() * 1000.0)


def _tokens(obs: dict[str, Any]) -> int:
    usage = obs.get("usageDetails") or obs.get("usage") or {}
    if isinstance(usage, dict):
        for key in ("total", "totalTokens"):
            if usage.get(key):
                return int(usage[key])
        parts = [usage.get(k) for k in ("input", "output", "promptTokens", "completionTokens")]
        return int(sum(int(p) for p in parts if isinstance(p, (int, float))))
    return 0


def samples_from_observations(observations: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    traces: dict[str, dict[str, Any]] = {}
    for obs in observations or []:
        if not isinstance(obs, dict):
            continue
        tid = str(obs.get("traceId") or obs.get("id"))
        t = traces.setdefault(tid, {"agent": None, "ttft": None, "first": None, "tokens": 0, "vector": [],
                                    "error": False})
        meta = obs.get("metadata") if isinstance(obs.get("metadata"), dict) else {}
        agent = meta.get("agent") or (meta.get("requester_metadata") or {}).get("agent") if isinstance(meta, dict) else None
        if agent and not t["agent"]:
            t["agent"] = str(agent)
        if obs.get("level") == "ERROR":
            t["error"] = True
        otype = obs.get("type")
        if otype == "GENERATION":
            t["tokens"] += _tokens(obs)
            start = to_datetime(obs.get("startTime"))
            ttft = _ms(obs.get("startTime"), obs.get("completionStartTime"))
            if ttft is not None and start is not None and (t["first"] is None or start < t["first"]):
                t["first"], t["ttft"] = start, ttft
        elif otype == "SPAN" and any(h in str(obs.get("name", "")).lower() for h in _VECTOR_HINTS):
            d = _ms(obs.get("startTime"), obs.get("endTime"))
            if d is not None:
                t["vector"].append(d)
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in traces.values():
        if t["ttft"] is None and not t["tokens"]:
            continue
        out[t["agent"] or UNATTRIBUTED].append({
            "ttft_ms": t["ttft"],
            "tokens": t["tokens"],
            "vector_latency_ms": (sum(t["vector"]) / len(t["vector"])) if t["vector"] else None,
            "success": not t["error"],
            "eur": 0.0,  # Langfuse costs are not in EUR; FinOps uses the gateway metrics
        })
    return dict(out)


class LangfuseClient(HttpSource):
    name = "langfuse"

    def __init__(self, base_url: str | None, public_key: str | None, secret_key: str | None, **kwargs: Any) -> None:
        super().__init__(base_url, **kwargs)
        self._auth = (public_key, secret_key) if public_key and secret_key else None

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self._auth)

    def health(self) -> bool:
        if not self.configured:
            return False
        try:
            payload = self._request_json("/api/public/health")
        except SourceError:
            return False
        return isinstance(payload, dict) and str(payload.get("status", "OK")).upper() == "OK"

    def observations(self, since: dt.datetime, obs_type: str | None = None, max_pages: int = 5) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            params: dict[str, Any] = {"fromStartTime": since.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                                      "limit": 100, "page": page}
            if obs_type:
                params["type"] = obs_type
            payload = self._request_json("/api/public/observations", params=params, auth=self._auth)
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list):
                raise SourceError("langfuse: unexpected answer")
            out.extend(d for d in data if isinstance(d, dict))
            total_pages = ((payload.get("meta") or {}).get("totalPages")) or 1
            if page >= int(total_pages):
                break
        return out

    def agent_samples(self, since: dt.datetime) -> dict[str, list[dict[str, Any]]]:
        observations = self.observations(since, "GENERATION") + self.observations(since, "SPAN", max_pages=2)
        return samples_from_observations(observations)
