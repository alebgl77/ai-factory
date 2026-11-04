"""Prometheus HTTP API connector (instant and range queries). Internal service, no auth."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

from .base import HttpSource, SourceError


def _num(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def parse_vector(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise SourceError("prometheus: invalid answer")
    data = payload.get("data") or {}
    out = []
    if data.get("resultType") == "vector":
        for item in data.get("result") or []:
            value = _num((item.get("value") or [None, None])[1])
            if value is not None:
                out.append({"metric": dict(item.get("metric") or {}), "value": value})
    elif data.get("resultType") == "scalar":
        value = _num((data.get("result") or [None, None])[1])
        if value is not None:
            out.append({"metric": {}, "value": value})
    return out


def parse_matrix(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise SourceError("prometheus: invalid answer")
    out = []
    for item in (payload.get("data") or {}).get("result") or []:
        values = []
        for ts, raw in item.get("values") or []:
            value = _num(raw)
            if value is not None:
                values.append((float(ts), value))
        out.append({"metric": dict(item.get("metric") or {}), "values": values})
    return out


class PrometheusClient(HttpSource):
    name = "prometheus"

    def health(self) -> bool:
        return self._request_ok("/-/ready")

    def query(self, promql: str, at: dt.datetime | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"query": promql}
        if at is not None:
            params["time"] = at.timestamp()
        return parse_vector(self._request_json("/api/v1/query", params=params))

    def query_range(self, promql: str, start: dt.datetime, end: dt.datetime, step_s: int) -> list[dict[str, Any]]:
        params = {"query": promql, "start": start.timestamp(), "end": end.timestamp(), "step": max(1, int(step_s))}
        return parse_matrix(self._request_json("/api/v1/query_range", params=params))
