"""Prometheus exposition of the factory KPIs (CONTRACT section 5), computed from a snapshot."""

from __future__ import annotations

from typing import Any, Iterable

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

from .audit import ACTIONS
from .engines.risk import TIERS


def families(snap: dict[str, Any]) -> list[GaugeMetricFamily]:
    out: list[GaugeMetricFamily] = []

    cost = GaugeMetricFamily("factory_usecase_cost_eur", "Cost per use case over the current period (EUR)",
                             labels=["usecase", "cost_center", "kind"])
    tokens = GaugeMetricFamily("factory_usecase_tokens_total", "Tokens per use case over the current period",
                               labels=["usecase", "direction"])
    for row in (snap.get("costs") or {}).get("by_usecase") or []:
        uc, cc = str(row.get("usecase")), str(row.get("cost_center") or "")
        cost.add_metric([uc, cc, "llm"], float(row.get("llm_eur") or 0))
        cost.add_metric([uc, cc, "gpu"], float(row.get("gpu_eur") or 0))
        cost.add_metric([uc, cc, "platform"], float(row.get("platform_eur") or 0))
        tokens.add_metric([uc, "in"], float(row.get("tokens_in") or 0))
        tokens.add_metric([uc, "out"], float(row.get("tokens_out") or 0))
    out += [cost, tokens]

    waste = GaugeMetricFamily("factory_waste_alerts", "Active waste alerts", labels=["rule", "severity"])
    counts: dict[tuple[str, str], int] = {}
    for a in (snap.get("costs") or {}).get("alerts") or []:
        k = (str(a.get("rule")), str(a.get("severity")))
        counts[k] = counts.get(k, 0) + 1
    for (rule, sev), n in sorted(counts.items()):
        waste.add_metric([rule, sev], float(n))
    out.append(waste)

    tiers = GaugeMetricFamily("factory_risk_usecases", "Use cases per AI Act risk tier", labels=["tier"])
    reeval = GaugeMetricFamily("factory_reevaluation_required", "1 when the risk must be re-assessed",
                               labels=["usecase"])
    tier_counts = {t: 0 for t in TIERS}
    for item in snap.get("registry") or []:
        risk = item.get("risk") or {}
        tier_counts[risk.get("tier", "minimal")] = tier_counts.get(risk.get("tier", "minimal"), 0) + 1
        reeval.add_metric([str(item.get("id"))], 1.0 if risk.get("reevaluation_required") else 0.0)
    for t, n in tier_counts.items():
        tiers.add_metric([t], float(n))
    out += [tiers, reeval]

    gate = GaugeMetricFamily("factory_pipeline_gate_pass", "1 when the eval gate passed", labels=["usecase"])
    rollbacks = GaugeMetricFamily("factory_rollbacks_total", "Automatic rollbacks in the history window",
                                  labels=["usecase"])
    pipe = snap.get("pipeline") or {}
    rb_counts: dict[str, int] = {}
    for rb in pipe.get("rollbacks") or []:
        rb_counts[str(rb.get("usecase"))] = rb_counts.get(str(rb.get("usecase")), 0) + 1
    for item in pipe.get("items") or []:
        uc = str(item.get("usecase"))
        gate.add_metric([uc], 1.0 if item.get("gate_passed") else 0.0)
        rb_counts.setdefault(uc, 0)
    for uc, n in sorted(rb_counts.items()):
        rollbacks.add_metric([uc], float(n))
    out += [gate, rollbacks]

    skills = GaugeMetricFamily("factory_skills_coverage_ratio", "AI Act Art. 4 module coverage per role",
                               labels=["role"])
    for role in (snap.get("skills") or {}).get("roles") or []:
        if role.get("coverage") is not None:
            skills.add_metric([str(role.get("role"))], float(role["coverage"]))
    out.append(skills)

    ttft = GaugeMetricFamily("factory_agent_ttft_seconds", "Time to first token per agent", labels=["agent", "quantile"])
    tpt = GaugeMetricFamily("factory_agent_tokens_per_task", "Tokens per task per agent", labels=["agent", "quantile"])
    vec = GaugeMetricFamily("factory_vector_latency_seconds", "Vector search latency", labels=["quantile"])
    ao = snap.get("agentops") or {}
    for a in ao.get("by_agent") or []:
        ttft.add_metric([str(a.get("agent")), "0.95"], float(a.get("ttft_ms_p95") or 0) / 1000.0)
        tpt.add_metric([str(a.get("agent")), "0.95"], float(a.get("tokens_per_task_p95") or 0))
    g = ao.get("global") or {}
    if g.get("tasks"):
        ttft.add_metric(["all", "0.5"], float(g.get("ttft_ms_p50") or 0) / 1000.0)
        ttft.add_metric(["all", "0.95"], float(g.get("ttft_ms_p95") or 0) / 1000.0)
        tpt.add_metric(["all", "0.95"], float(g.get("tokens_per_task_p95") or 0))
        vec.add_metric(["0.5"], float(g.get("vector_latency_ms_p50") or 0) / 1000.0)
        vec.add_metric(["0.95"], float(g.get("vector_latency_ms_p95") or 0) / 1000.0)
    out += [ttft, tpt, vec]

    info = GaugeMetricFamily("factory_snapshot_info", "Snapshot provenance", labels=["mode", "version"])
    meta = snap.get("meta") or {}
    info.add_metric([str(meta.get("mode")), str(meta.get("version"))], 1.0)
    out.append(info)
    return out


def security_families(audit_status: dict[str, Any], audit_counts: dict[str, int],
                      denials: dict[str, int]) -> list[Metric]:
    """Audit trail and access-control metrics (no identity, no secret in any label)."""
    events = CounterMetricFamily("factory_audit_events", "Records of the hash-chained audit log per action",
                                 labels=["action"])
    for action in sorted(set(ACTIONS) | set(audit_counts)):
        events.add_metric([action], float(audit_counts.get(action, 0)))
    chain = GaugeMetricFamily("factory_audit_chain_ok", "1 when the audit hash chain verifies, else 0")
    chain.add_metric([], 1.0 if audit_status.get("chain_ok") else 0.0)
    denied = CounterMetricFamily("factory_security_denied", "Requests refused by the API security controls",
                                 labels=["reason"])
    for reason in sorted(set(DENIAL_REASONS) | set(denials)):
        denied.add_metric([reason], float(denials.get(reason, 0)))
    return [events, chain, denied]


DENIAL_REASONS = ("untrusted_source", "unauthenticated", "invalid_identity", "forbidden", "separation_of_duties",
                  "rate_limited")


class _SnapshotCollector(Collector):
    def __init__(self, snap: dict[str, Any], extra: list[Metric] | None = None) -> None:
        self.snap = snap
        self.extra = extra or []

    def collect(self) -> Iterable[Metric]:
        return [*families(self.snap), *self.extra]


def render(snap: dict[str, Any], extra: list[Metric] | None = None) -> bytes:
    registry = CollectorRegistry(auto_describe=False)
    registry.register(_SnapshotCollector(snap, extra))
    return generate_latest(registry)
