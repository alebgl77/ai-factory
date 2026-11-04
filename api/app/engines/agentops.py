"""Agent Ops engine: TTFT, tokens per task, vector latency, success rate, SLO breaches.

Input samples, per agent: a list of task records
    {ttft_ms, tokens, vector_latency_ms, success: bool, eur}
"""

from __future__ import annotations

from typing import Any

from ..util import mean, percentile, r2, r4, safe_div

SLO_KEYS = ("ttft_ms_p95", "vector_latency_ms_p95", "tokens_per_task_p95")


def agent_stats(samples: list[dict[str, Any]], slo: dict[str, Any]) -> dict[str, Any]:
    ttft = [float(s["ttft_ms"]) for s in samples if s.get("ttft_ms") is not None]
    tokens = [float(s["tokens"]) for s in samples if s.get("tokens") is not None]
    vector = [float(s["vector_latency_ms"]) for s in samples if s.get("vector_latency_ms") is not None]
    successes = [bool(s.get("success", True)) for s in samples]
    cost = sum(float(s.get("eur") or 0) for s in samples)
    stats = {
        "ttft_ms_p50": round(percentile(ttft, 0.5)),
        "ttft_ms_p95": round(percentile(ttft, 0.95)),
        "tokens_per_task_avg": round(mean(tokens)),
        "tokens_per_task_p50": round(percentile(tokens, 0.5)),
        "tokens_per_task_p95": round(percentile(tokens, 0.95)),
        "vector_latency_ms_p50": round(percentile(vector, 0.5)),
        "vector_latency_ms_p95": round(percentile(vector, 0.95)),
        "tasks": len(samples),
        "success_rate": r4(safe_div(sum(successes), len(successes))),
        "eur_per_task": r4(safe_div(cost, len(samples))),
    }
    breaches = []
    for key in SLO_KEYS:
        limit = slo.get(key)
        if limit is not None and samples and stats[key] > float(limit):
            if key == "vector_latency_ms_p95" and not vector:
                continue
            breaches.append(key)
    stats["breaches"] = breaches
    return stats


def evaluate_agentops(samples_by_agent: dict[str, list[dict[str, Any]]], agents: list[dict[str, Any]],
                      slo: dict[str, Any], series: list[dict[str, Any]] | None = None,
                      window: str = "1h") -> dict[str, Any]:
    agent_meta = {str(a.get("id")): a for a in agents or []}
    by_agent = []
    all_samples: list[dict[str, Any]] = []
    for agent_id in sorted(samples_by_agent):
        samples = samples_by_agent[agent_id] or []
        all_samples.extend(samples)
        stats = agent_stats(samples, slo)
        meta = agent_meta.get(agent_id, {})
        by_agent.append({
            "agent": agent_id,
            "name": meta.get("name"),
            "usecase": meta.get("usecase"),
            "ttft_ms_p95": stats["ttft_ms_p95"],
            "tokens_per_task_p95": stats["tokens_per_task_p95"],
            "vector_latency_ms_p95": stats["vector_latency_ms_p95"],
            "success_rate": stats["success_rate"],
            "tasks": stats["tasks"],
            "eur_per_task": stats["eur_per_task"],
            "breaches": stats["breaches"],
        })
    g = agent_stats(all_samples, slo)
    glob = {k: g[k] for k in ("ttft_ms_p50", "ttft_ms_p95", "tokens_per_task_avg", "tokens_per_task_p95",
                              "vector_latency_ms_p50", "vector_latency_ms_p95", "tasks", "success_rate")}
    glob["eur_per_task"] = g["eur_per_task"]
    glob["breaches"] = g["breaches"]
    return {
        "window": window,
        "slo": {k: slo.get(k) for k in SLO_KEYS},
        "global": glob,
        "by_agent": by_agent,
        "series": [dict(p) for p in series or []],
        "breaching_agents": [a["agent"] for a in by_agent if a["breaches"]],
        "cost_eur_window": r2(sum(float(s.get("eur") or 0) for s in all_samples)),
    }
