"""Data loop engine: drift signal -> remediation action. Never retrains, never fine-tunes.

Allowed actions are exactly: reindex, update_eval_set, change_routing, human_review
(CONTRACT principles). Any other action, in particular 'retrain' or 'fine_tune', raises
ForbiddenActionError, whatever the configuration says.
"""

from __future__ import annotations

from typing import Any

from ..i18n import DEFAULT_LANG, msg

ALLOWED_ACTIONS = ("reindex", "update_eval_set", "change_routing", "human_review")
FORBIDDEN_ACTIONS = ("retrain", "fine_tune", "finetune", "fine-tune", "fine_tuning", "training", "train",
                     "continued_pretraining", "rlhf", "lora", "distill")
SIGNALS = ("retrieval_hit_rate", "query_drift", "corpus_freshness", "eval_failure_cluster",
           "cost_drift", "latency_drift")
STATUSES = ("done", "running", "proposed")

# signal -> (base action, direction): "below" means the signal is bad when value < threshold.
SIGNAL_RULES: dict[str, tuple[str, str]] = {
    "retrieval_hit_rate": ("reindex", "below"),
    "corpus_freshness": ("reindex", "above"),
    "query_drift": ("update_eval_set", "above"),
    "eval_failure_cluster": ("update_eval_set", "above"),
    "cost_drift": ("change_routing", "above"),
    "latency_drift": ("change_routing", "above"),
}
DEFAULT_THRESHOLDS = {
    "retrieval_hit_rate": 0.75,   # share of queries with a relevant chunk in top-k
    "corpus_freshness": 0.10,     # share of documents older than their refresh SLA
    "query_drift": 0.25,          # embedding centroid distance vs the evaluation set
    "eval_failure_cluster": 5,    # failures sharing one topic cluster
    "cost_drift": 20.0,           # % cost increase per request
    "latency_drift": 20.0,        # % p95 latency increase
}
SEVERE_FACTOR = 2.0


def action_detail(action: str, lang: str = DEFAULT_LANG) -> str:
    """Description of an allowed action (catalogue keys ``dataloop.action.*``)."""
    return msg(lang, f"dataloop.action.{action}")


class ForbiddenActionError(ValueError):
    """Raised for any action outside ALLOWED_ACTIONS (retraining and fine-tuning are disabled)."""


def validate_action(action: str, fine_tuning: bool = False, lang: str = DEFAULT_LANG) -> str:
    """Return the action when allowed; raise ForbiddenActionError otherwise.

    ``fine_tuning`` is accepted for clarity of intent but cannot enable retraining: the
    reference factory has no training path at all.
    """
    key = str(action or "").strip().lower()
    if key in FORBIDDEN_ACTIONS or "train" in key or "fine" in key:
        raise ForbiddenActionError(msg(lang, "dataloop.error.forbidden", action=action,
                                       fine_tuning=str(bool(fine_tuning)).lower()))
    if key not in ALLOWED_ACTIONS:
        raise ForbiddenActionError(msg(lang, "dataloop.error.unknown", action=action,
                                       allowed=", ".join(ALLOWED_ACTIONS)))
    return key


def is_breach(signal: str, value: float, threshold: float) -> bool:
    direction = SIGNAL_RULES.get(signal, ("human_review", "above"))[1]
    return value < threshold if direction == "below" else value > threshold


def is_severe(signal: str, value: float, threshold: float) -> bool:
    direction = SIGNAL_RULES.get(signal, ("human_review", "above"))[1]
    if direction == "below":
        return value <= threshold / SEVERE_FACTOR
    return threshold > 0 and value >= threshold * SEVERE_FACTOR


def decide_action(signal: str, value: float, threshold: float, tier: str | None,
                  lang: str = DEFAULT_LANG) -> tuple[str, str]:
    """Map a drift signal to an action. Returns (action, rationale in ``lang``)."""
    if signal not in SIGNAL_RULES:
        return "human_review", msg(lang, "dataloop.rationale.unknown_signal")
    base, _direction = SIGNAL_RULES[signal]
    if is_severe(signal, value, threshold):
        return "human_review", msg(lang, "dataloop.rationale.severe", factor=SEVERE_FACTOR)
    if base == "change_routing" and tier in ("high", "prohibited"):
        return "human_review", msg(lang, "dataloop.rationale.high_risk_routing")
    return base, action_detail(base, lang)


def evaluate_signals(signals: list[dict[str, Any]], registry: list[dict[str, Any]],
                     factory_cfg: dict[str, Any], lang: str = DEFAULT_LANG, *,
                     verified: bool = False) -> dict[str, Any]:
    tiers = {item.get("id"): (item.get("risk") or {}).get("tier") for item in registry}
    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds.update({k: v for k, v in ((factory_cfg.get("dataloop") or {}).get("thresholds") or {}).items()
                       if isinstance(v, (int, float))})
    fine_tuning = bool((factory_cfg.get("factory") or {}).get("fine_tuning", False))
    events = []
    for s in signals or []:
        signal = str(s.get("signal"))
        value = float(s.get("value") or 0)
        threshold = float((thresholds.get(signal) if verified else s.get("threshold", thresholds.get(signal))) or 0)
        if not is_breach(signal, value, threshold):
            continue
        action, rationale = decide_action(signal, value, threshold, tiers.get(s.get("usecase")), lang)
        requested = None if verified else s.get("requested_action")
        if requested:
            # A source may suggest an action: it is validated (retrain/fine-tune are refused).
            action = validate_action(requested, fine_tuning, lang)
        validate_action(action, fine_tuning, lang)
        status = s.get("status") if not verified and s.get("status") in STATUSES else "proposed"
        if action == "human_review" and status == "done" and not s.get("reviewed_by"):
            status = "proposed"
        event = {
            "at": s.get("at"),
            "usecase": s.get("usecase"),
            "signal": signal,
            "value": value,
            "threshold": threshold,
            "action": action,
            "status": status,
            "detail": rationale if verified else s.get("detail") or rationale,
            "rationale": rationale,
        }
        if verified:
            event.update({key: s[key] for key in ("source", "observation_id", "review_revision")})
        events.append(event)
    events.sort(key=lambda e: str(e.get("at") or ""), reverse=True)
    counts = {a: sum(1 for e in events if e["action"] == a) for a in ALLOWED_ACTIONS}
    return {
        "events": events,
        "retraining": "disabled",
        "fine_tuning": False,
        "allowed_actions": list(ALLOWED_ACTIONS),
        "action_counts": counts,
        "thresholds": thresholds,
    }
