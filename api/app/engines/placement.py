"""Hybrid placement engine: cloud first, then VPS, then local; sensitive classes local only.

Policy (CONTRACT principles and factory.yaml ``placement``):
- a workload whose sensitivity is in ``sensitive_classes`` runs on the local GPU only; when the
  local target is down it is **queued**, never sent to the cloud or the VPS;
- other workloads follow ``order`` (default cloud -> vps -> local), skipping targets that are
  down and the VPS when the model needs more than ``vps_max_params_b`` billion parameters.
"""

from __future__ import annotations

from typing import Any

from ..i18n import DEFAULT_LANG, msg

TARGETS = ("cloud", "vps", "local")


def target_label(target: str, lang: str = DEFAULT_LANG, demo: bool = False) -> str:
    """Display label of a placement target (catalogue keys ``placement.target.*``)."""
    key = f"placement.target.{target}_demo" if demo and target == "cloud" else f"placement.target.{target}"
    return msg(lang, key)


# Minimal model size (billion parameters) needed by a task type when the catalog does not say.
MIN_PARAMS_BY_TASK = {
    "routing": 3, "classification": 3, "extraction": 7, "summarization": 8,
    "rag_qa": 8, "generation": 14, "agent": 30,
}
SENSITIVE_ALIAS = "chat-sensitive"


def required_params_b(workload: dict[str, Any]) -> float | None:
    if workload.get("params_b") is not None:
        return float(workload["params_b"])
    task = workload.get("task_type")
    return float(MIN_PARAMS_BY_TASK[task]) if task in MIN_PARAMS_BY_TASK else None


def decide(workload: dict[str, Any], targets: list[dict[str, Any]], policy: dict[str, Any],
           lang: str = DEFAULT_LANG) -> dict[str, Any]:
    """Pure placement decision for one workload (reasons in ``lang``)."""
    status = {str(t.get("id")): str(t.get("status", "down")) for t in targets or []}
    sensitive_classes = list(policy.get("sensitive_classes") or ["C3", "C4"])
    order = [t for t in (policy.get("order") or ["cloud", "vps", "local"]) if t in ("cloud", "vps", "local")]
    vps_max = policy.get("vps_max_params_b")
    sensitivity = workload.get("sensitivity")
    reasons: list[str] = []
    base = {
        "id": workload.get("id"),
        "usecase": workload.get("usecase"),
        "sensitivity": sensitivity,
    }

    if sensitivity in sensitive_classes:
        reasons.append(msg(lang, "placement.reason.sensitive_local_only", sensitivity=sensitivity))
        local = status.get("local", "down")
        if local in ("up", "degraded"):
            if local == "degraded":
                reasons.append(msg(lang, "placement.reason.local_degraded"))
            return {**base, "target": "local", "status": "placed", "fallback": [], "reasons": reasons,
                    "model_alias": SENSITIVE_ALIAS, "queued": False}
        reasons.append(msg(lang, "placement.reason.local_down_queued"))
        return {**base, "target": "queued", "status": "queued", "fallback": [], "reasons": reasons,
                "model_alias": SENSITIVE_ALIAS, "queued": True}

    need = required_params_b(workload)
    eligible: list[str] = []
    for target in order:
        st = status.get(target, "down")
        if st == "down":
            reasons.append(msg(lang, "placement.reason.target_down", target=target))
            continue
        if target == "vps" and vps_max is not None:
            if need is None:
                reasons.append(msg(lang, "placement.reason.vps_unknown_size"))
                continue
            if need > float(vps_max):
                reasons.append(msg(lang, "placement.reason.vps_too_small", need=need, max=float(vps_max)))
                continue
        eligible.append(target)
    # Healthy targets first, degraded ones as last resort, keeping the policy order.
    eligible.sort(key=lambda t: (0 if status.get(t) == "up" else 1, order.index(t)))
    if not eligible:
        reasons.append(msg(lang, "placement.reason.no_target_queued"))
        return {**base, "target": "queued", "status": "queued", "fallback": [], "reasons": reasons,
                "model_alias": workload.get("model_alias"), "queued": True}
    target = eligible[0]
    if status.get(target) == "degraded":
        reasons.append(msg(lang, "placement.reason.degraded_chosen", target=target))
    rank = order.index(target)
    key = "placement.reason.order_preferred" if rank == 0 else "placement.reason.order"
    reasons.insert(0, msg(lang, key, sensitivity=sensitivity or "C?", order=" > ".join(order), target=target))
    return {**base, "target": target, "status": "placed", "fallback": eligible[1:], "reasons": reasons,
            "model_alias": workload.get("model_alias"), "queued": False}


def build_workloads(registry: list[dict[str, Any]], models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One workload per active use case (not cadrage / retired)."""
    params_by_alias: dict[str, Any] = {}
    for m in models or []:
        if m.get("alias") and m.get("alias") not in params_by_alias:
            params_by_alias[m["alias"]] = m.get("params_b")
    out = []
    n = 0
    for item in registry:
        if item.get("stage") in ("cadrage", "retired"):
            continue
        n += 1
        alias = (item.get("model") or {}).get("alias")
        out.append({
            "id": f"wl-{n:02d}",
            "usecase": item.get("id"),
            "sensitivity": item.get("sensitivity"),
            "task_type": item.get("task_type"),
            "model_alias": alias,
            "params_b": params_by_alias.get(alias) if alias in params_by_alias else None,
        })
    return out


def evaluate_placement(registry: list[dict[str, Any]], targets: list[dict[str, Any]], models: list[dict[str, Any]],
                       policy: dict[str, Any], lang: str = DEFAULT_LANG) -> dict[str, Any]:
    workloads = []
    for wl in build_workloads(registry, models):
        decision = decide(wl, targets, policy, lang)
        decision["params_b"] = wl.get("params_b")
        decision["task_type"] = wl.get("task_type")
        workloads.append(decision)
    counts = {t: sum(1 for w in workloads if w["target"] == t) for t in ("cloud", "vps", "local", "queued")}
    return {
        "policy": {
            "order": list(policy.get("order") or []),
            "sensitive_classes": list(policy.get("sensitive_classes") or []),
            "vps_max_params_b": policy.get("vps_max_params_b"),
        },
        "targets": [dict(t) for t in targets],
        "workloads": workloads,
        "counts": counts,
    }
