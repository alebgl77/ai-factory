"""Collect live inputs from the connectors. Every collector fails soft (returns None)."""

from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict
from typing import Any, Callable

from .connectors import Connectors, SourceError
from .util import UNATTRIBUTED

log = logging.getLogger("factory.live")

DCGM = {
    "util_pct": "DCGM_FI_DEV_GPU_UTIL",
    "temp_c": "DCGM_FI_DEV_GPU_TEMP",
    "power_w": "DCGM_FI_DEV_POWER_USAGE",
    "fb_used": "DCGM_FI_DEV_FB_USED",
    "fb_free": "DCGM_FI_DEV_FB_FREE",
}


def _soft(fn: Callable[[], Any], what: str) -> Any:
    try:
        return fn()
    except (SourceError, ValueError, KeyError, TypeError) as exc:
        log.info("live source %s unavailable: %s", what, exc.__class__.__name__)
        return None
    except Exception:  # never let a connector crash the snapshot
        log.warning("live source %s failed", what, exc_info=False)
        return None


def gpu_from_prometheus(conns: Connectors, now: dt.datetime) -> dict[str, Any] | None:
    """DCGM metrics (real dcgm-exporter or sim/gpu-sim). The ``gpu_source`` label set by the
    Prometheus relabelling (sim | dcgm | dcgm-remote) tells simulated telemetry apart."""
    devices: dict[tuple[str, str], dict[str, Any]] = {}
    for key, metric in DCGM.items():
        for s in conns.prometheus.query(f"max by (gpu, modelName, gpu_source) ({metric})"):
            src = str(s["metric"].get("gpu_source") or "dcgm")
            gid = str(s["metric"].get("gpu", "0"))
            d = devices.setdefault((src, gid), {"gpu": gid, "source": src,
                                                "name": s["metric"].get("modelName") or f"GPU {gid}"})
            d[key] = s["value"]
    if not devices:
        return None
    gids = [g for _src, g in devices]
    out = []
    for (src, gid) in sorted(devices):
        d = devices[(src, gid)]
        used = float(d.get("fb_used") or 0) / 1024.0
        free = float(d.get("fb_free") or 0) / 1024.0
        dev_id = f"gpu{gid}" if gids.count(gid) == 1 else f"gpu{gid}-{src}"
        out.append({"id": dev_id, "name": d["name"], "util_pct": round(float(d.get("util_pct") or 0), 1),
                    "temp_c": round(float(d.get("temp_c") or 0), 1), "power_w": round(float(d.get("power_w") or 0), 1),
                    "mem_used_gb": round(used, 1), "mem_total_gb": round(used + free, 1), "sensitive_sessions": 0,
                    "telemetry": "sim" if src == "sim" else "dcgm"})
    series = []
    start = now - dt.timedelta(hours=23)
    util = conns.prometheus.query_range(f"avg by (gpu) ({DCGM['util_pct']})", start, now, 3600)
    power = conns.prometheus.query_range(f"avg by (gpu) ({DCGM['power_w']})", start, now, 3600)
    pmap = {(str(s["metric"].get("gpu", "0")), ts): v for s in power for ts, v in s["values"]}
    for s in util:
        gid = str(s["metric"].get("gpu", "0"))
        for ts, v in s["values"]:
            t = dt.datetime.fromtimestamp(ts, tz=now.tzinfo).replace(microsecond=0).isoformat()
            series.append({"t": t, "device": f"gpu{gid}", "util_pct": round(v, 1),
                           "power_w": round(pmap.get((gid, ts), 0.0), 1)})
    source = "sim" if all(src == "sim" for src, _g in devices) else "dcgm"
    return {"devices": out, "series": series, "source": source}


def targets_from_prometheus(conns: Connectors) -> dict[str, dict[str, Any]] | None:
    """Per-deployment health (cloud | vps | local) from the gateway callback metrics."""
    window = "15m"
    reqs = conns.prometheus.query(f"sum by (deployment) (increase(factory_llm_requests_total[{window}]))")
    errs = conns.prometheus.query(
        f'sum by (deployment) (increase(factory_llm_requests_total{{status!="success"}}[{window}]))')
    lat = conns.prometheus.query("histogram_quantile(0.95, sum by (le, deployment) "
                                 f"(rate(factory_llm_latency_seconds_bucket[{window}])))")
    out: dict[str, dict[str, Any]] = {}
    for s in reqs:
        dep = str(s["metric"].get("deployment"))
        if dep in ("cloud", "vps", "local"):
            out.setdefault(dep, {})["requests"] = s["value"]
    for s in errs:
        dep = str(s["metric"].get("deployment"))
        if dep in out:
            out[dep]["errors"] = s["value"]
    for s in lat:
        dep = str(s["metric"].get("deployment"))
        if dep in out:
            out[dep]["latency_ms_p95"] = round(s["value"] * 1000.0)
    for v in out.values():
        total = float(v.get("requests") or 0)
        ratio = float(v.get("errors") or 0) / total if total else 0.0
        v["error_ratio"] = round(ratio, 4)
        v["status"] = "down" if total and ratio > 0.5 else ("degraded" if ratio > 0.05 else "up")
    return out or None


def usage_from_prometheus(conns: Connectors, today: dt.date, days: int, tz: dt.tzinfo) -> list[dict[str, Any]] | None:
    """Daily records from the LiteLLM custom callback metrics (factory_llm_*)."""
    end = dt.datetime.combine(today + dt.timedelta(days=1), dt.time(0, 0), tzinfo=tz)
    start = end - dt.timedelta(days=days - 1)
    step = 86400

    def day_of(ts: float) -> str:
        return (dt.datetime.fromtimestamp(ts, tz=tz) - dt.timedelta(seconds=1)).date().isoformat()

    rows: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"requests": 0, "retries": 0, "tokens_in": 0,
                                                                       "tokens_out": 0, "llm_eur": 0.0,
                                                                       "_model_cost": defaultdict(float)})
    cost = conns.prometheus.query_range("sum by (usecase, model) (increase(factory_llm_cost_eur_total[1d]))",
                                        start, end, step)
    tokens = conns.prometheus.query_range("sum by (usecase, direction) (increase(factory_llm_tokens_total[1d]))",
                                          start, end, step)
    reqs = conns.prometheus.query_range("sum by (usecase) (increase(factory_llm_requests_total[1d]))",
                                        start, end, step)
    if not (cost or tokens or reqs):
        return None
    for s in cost:
        uc = str(s["metric"].get("usecase") or UNATTRIBUTED)
        for ts, v in s["values"]:
            r = rows[(day_of(ts), uc)]
            r["llm_eur"] += v
            r["_model_cost"][str(s["metric"].get("model") or "")] += v
    for s in tokens:
        uc = str(s["metric"].get("usecase") or UNATTRIBUTED)
        key = "tokens_in" if s["metric"].get("direction") in ("in", "input", "prompt") else "tokens_out"
        for ts, v in s["values"]:
            rows[(day_of(ts), uc)][key] += int(round(v))
    for s in reqs:
        uc = str(s["metric"].get("usecase") or UNATTRIBUTED)
        for ts, v in s["values"]:
            rows[(day_of(ts), uc)]["requests"] += int(round(v))
    out = []
    for (d, uc), r in sorted(rows.items()):
        mc = r.pop("_model_cost")
        model = max(mc, key=lambda m: mc[m]) if mc else None
        out.append({"date": d, "usecase": uc, "model_alias": model or None, **r})
    return out if any(r["llm_eur"] or r["requests"] or r["tokens_in"] for r in out) else None


def collect_live(conns: Connectors, sources: dict[str, bool], *, today: dt.date, now: dt.datetime, days: int,
                 tz: dt.tzinfo) -> dict[str, Any]:
    live: dict[str, Any] = {}
    if sources.get("prometheus"):
        live["gpu"] = _soft(lambda: gpu_from_prometheus(conns, now), "gpu")
        live["usage"] = _soft(lambda: usage_from_prometheus(conns, today, days, tz), "usage")
        live["targets"] = _soft(lambda: targets_from_prometheus(conns), "targets")
    if sources.get("langfuse"):
        samples = _soft(lambda: conns.langfuse.agent_samples(now - dt.timedelta(hours=1)), "langfuse")
        live["agent_samples"] = samples or None
    if sources.get("qdrant"):
        live["qdrant_collections"] = _soft(lambda: conns.qdrant.collections(), "qdrant")
    if sources.get("grafana"):
        live["grafana_dashboards"] = _soft(lambda: conns.grafana.dashboards(), "grafana")
    if sources.get("litellm"):
        live["litellm_models"] = _soft(lambda: conns.litellm.models(), "litellm")
        if not live.get("usage"):
            # Fallback: DB-backed spend logs of the gateway (amounts in the gateway's price unit).
            start = today - dt.timedelta(days=days - 1)
            spend = _soft(lambda: conns.litellm.spend_daily(start, today), "litellm-spend")
            if spend:
                live["usage"] = [{"date": r["date"], "usecase": r["usecase"], "requests": r["requests"],
                                  "tokens_in": r["tokens_in"], "tokens_out": r["tokens_out"],
                                  "llm_eur": r["spend"]} for r in spend]
                live["usage_origin"] = "litellm_spend_logs"
    return {k: v for k, v in live.items() if v is not None}
