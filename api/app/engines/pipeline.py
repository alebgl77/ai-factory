"""LLMOps / RAG pipeline engine: 7 stages, tiered eval gate, deploy gate, auto rollback.

Input "runs" (one per use case release) come from the demo generator or live sources:

    {usecase, version, previous_version, prompt_version, agent_version, model_version,
     in_progress_stage?: str, failed_stage?: str,
     stages: {ingestion: {at, detail}, indexing: {...}, versioning: {...}, deploy: {at}, feedback: {at}},
     eval: {at, metrics: {faithfulness, answer_relevancy, context_precision, judge_score, redteam_pass}},
     online: {at, thumbs_down_ratio, faithfulness_drop, error_rate}}

Pure functions: the engine never deploys anything, it evaluates gates and rollback rules.
"""

from __future__ import annotations

from typing import Any

from ..i18n import DEFAULT_LANG, msg
from .risk import tier_label

STAGES = ["ingestion", "indexing", "versioning", "eval_gate", "deploy", "feedback", "rollback_guard"]
GATE_METRICS = ["faithfulness", "answer_relevancy", "context_precision", "judge_score", "redteam_pass"]
ROLLBACK_RULES = [
    # (trigger name, online metric key, config key)
    ("thumbs_down_ratio", "thumbs_down_ratio", "thumbs_down_ratio_max"),
    ("online_faithfulness_drop", "faithfulness_drop", "online_faithfulness_drop_max"),
    ("error_rate", "error_rate", "error_rate_max"),
]
_UPSTREAM = STAGES[:3]


def gate_thresholds(tier: str, eval_gates: dict[str, Any]) -> dict[str, float] | None:
    """Thresholds of the eval gate for a risk tier; None for a prohibited system."""
    if tier == "prohibited":
        return None
    gates = eval_gates or {}
    th = gates.get(tier)
    if not th:
        # Unknown tier: be conservative and use the strictest configured gate.
        th = gates.get("high") or gates.get("limited") or gates.get("minimal") or {}
    return {k: float(v) for k, v in th.items()}


def trigger_label(trigger: str | None, lang: str = DEFAULT_LANG) -> str:
    """Human-readable rollback trigger (the ``trigger`` field itself stays an enum id)."""
    known = {t for t, _key, _cfg in ROLLBACK_RULES}
    return msg(lang, f"pipeline.trigger.{trigger}") if trigger in known else str(trigger)


def evaluate_gate(metrics: dict[str, Any] | None, thresholds: dict[str, float],
                  lang: str = DEFAULT_LANG) -> tuple[bool, list[str]]:
    failures = []
    metrics = metrics or {}
    for key, th in thresholds.items():
        value = metrics.get(key)
        if value is None:
            failures.append(msg(lang, "pipeline.gate.missing", metric=key))
        elif float(value) < th:
            failures.append(msg(lang, "pipeline.gate.below", metric=key, value=float(value), threshold=th))
    return not failures, failures


def check_rollback(online: dict[str, Any] | None, rollback_cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Return the first breached rollback rule, or None."""
    if not online:
        return None
    for trigger, key, cfg_key in ROLLBACK_RULES:
        if cfg_key not in (rollback_cfg or {}):
            continue
        value = online.get(key)
        threshold = float(rollback_cfg[cfg_key])
        if value is not None and float(value) > threshold:
            return {"trigger": trigger, "value": float(value), "threshold": threshold}
    return None


def _verified_review(run: dict[str, Any], assessment: dict[str, Any]) -> bool:
    # The authorized snapshot omits the reviewer identity. Receipt ingress separately
    # checks the protected subject proof and timestamp before recording evidence.
    return bool(assessment.get("decision") == "approved" and assessment.get("separation_of_duties") == "verified"
                and assessment.get("reviewed_revision") == run.get("review_revision")
                and assessment.get("reviewed_model_version") == run.get("model_version"))


def evaluate_release_gate(run: dict[str, Any], item: dict[str, Any], factory_cfg: dict[str, Any]) -> bool:
    """Require complete evidence and the server's exact four-eyes approval."""
    risk = item.get("risk") or {}
    thresholds = gate_thresholds(risk.get("tier", "high"), factory_cfg.get("eval_gates") or {})
    return bool(thresholds and not risk.get("deploy_block")
                and all((run.get("stages") or {}).get(key) for key in _UPSTREAM)
                and _verified_review(run, item.get("assessment") or {})
                and evaluate_gate((run.get("eval") or {}).get("metrics"), thresholds)[0])


def evaluate_run(run: dict[str, Any], risk: dict[str, Any], eval_gates: dict[str, Any],
                 rollback_cfg: dict[str, Any], lang: str = DEFAULT_LANG, *,
                 verified: bool = False) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Evaluate one release run against the gates. Returns (item, rollback_event_or_None).

    Stage details are written in ``lang``; ``risk["reason"]`` must already be in that language.
    """
    stages_in = run.get("stages") or {}
    tier = risk.get("tier", "minimal")
    out_stages: list[dict[str, Any]] = []
    blocked_by: str | None = None
    rollback: dict[str, Any] | None = None

    def add(key: str, status: str, at: Any = None, detail: str | None = None, **extra: Any) -> None:
        out_stages.append({"key": key, "status": status, "at": at, "detail": detail, **extra})

    in_progress = run.get("in_progress_stage")
    failed_stage = run.get("failed_stage")
    upstream_ok = True
    for key in _UPSTREAM:
        info = stages_in.get(key) or {}
        if not upstream_ok:
            add(key, "pending")
        elif verified and not info:
            add(key, "pending")
            upstream_ok = False
        elif failed_stage == key:
            add(key, "failed", info.get("at"), info.get("detail") or msg(lang, "pipeline.stage.failed"))
            upstream_ok = False
        elif in_progress == key:
            add(key, "running", info.get("at"), info.get("detail") or msg(lang, "pipeline.stage.running"))
            upstream_ok = False
        else:
            add(key, "passed", info.get("at"), info.get("detail"))

    # Eval gate (RAGAS + LLM-as-judge + red team), thresholds by AI Act tier.
    thresholds = gate_thresholds(tier, eval_gates)
    ev = run.get("eval") or {}
    gate_passed = False
    if thresholds is None:
        add("eval_gate", "blocked", ev.get("at"), msg(lang, "pipeline.eval.prohibited"),
            metrics=ev.get("metrics") or {}, thresholds={})
        blocked_by = "prohibited_practice"
    elif not upstream_ok:
        add("eval_gate", "pending", None, None, metrics={}, thresholds=thresholds)
    elif in_progress == "eval_gate":
        add("eval_gate", "running", ev.get("at"), msg(lang, "pipeline.eval.running"), metrics=ev.get("metrics") or {},
            thresholds=thresholds)
    elif verified and not ev.get("at"):
        add("eval_gate", "pending", thresholds=thresholds)
    else:
        ok, failures = evaluate_gate(ev.get("metrics"), thresholds, lang)
        if verified and not thresholds:
            ok = False
        gate_passed = ok
        add("eval_gate", "passed" if ok else "failed", ev.get("at"),
            msg(lang, "pipeline.eval.passed", tier=tier_label(tier, lang)) if ok
            else msg(lang, "pipeline.eval.failed", failures=", ".join(failures)),
            metrics=ev.get("metrics") or {}, thresholds=thresholds, tier=tier)

    # Deploy gate: blocked when the risk must be re-assessed (model version change).
    deploy_info = stages_in.get("deploy") or {}
    reviewed = (run.get("reviewed_model_version") if run.get("reviewed_model_version") is not None
                else risk.get("reviewed_model_version"))
    model_version = run.get("model_version")
    candidate_unreviewed = model_version is not None and reviewed is not None and str(model_version) != str(reviewed)
    deployed = False
    if blocked_by == "prohibited_practice":
        add("deploy", "blocked", None, msg(lang, "pipeline.deploy.prohibited"))
    elif risk.get("reevaluation_required") or candidate_unreviewed:
        blocked_by = "risk_reassessment"
        why = risk.get("reason") if risk.get("reevaluation_required") else msg(
            lang, "risk.reason.model_not_reviewed", version=str(model_version), last=str(reviewed))
        add("deploy", "blocked", None, msg(lang, "pipeline.deploy.reassessment", reason=why))
    elif risk.get("deploy_block") == "assessment_rejected":
        blocked_by = "assessment_rejected"
        add("deploy", "blocked", None, msg(lang, "pipeline.deploy.rejected"))
    elif verified and not _verified_review(run, risk.get("assessment") or {}):
        blocked_by = "risk_reassessment"
        add("deploy", "blocked", None, msg(lang, "pipeline.deploy.reassessment", reason=risk.get("reason") or ""))
    elif not gate_passed:
        if out_stages[-1]["status"] == "failed":
            blocked_by = "eval_gate"
            add("deploy", "blocked", None, msg(lang, "pipeline.deploy.eval_gate"))
        else:
            add("deploy", "pending")
    elif in_progress == "deploy":
        add("deploy", "running", deploy_info.get("at"), msg(lang, "pipeline.deploy.canary"))
    elif verified and not run.get("deployment_receipt"):
        add("deploy", "pending")
    else:
        deployed = True
        if verified:
            deploy_info = run["deployment_receipt"]
        add("deploy", "passed", deploy_info.get("at"), deploy_info.get("detail") or msg(lang, "pipeline.deploy.done"))

    # Feedback and rollback guard (online thumbs, faithfulness, errors).
    online = run.get("online")
    deployed_version = None if verified else run.get("previous_version")
    if deployed:
        deployed_version = run.get("version")
        if verified and run.get("rollback_receipt"):
            receipt = run["rollback_receipt"]
            add("feedback", "passed" if online else "pending", (online or {}).get("at"))
            add("rollback_guard", "passed", receipt["at"])
            rollback = {"at": receipt["at"], "usecase": run["usecase"], "from": run["version"],
                        "to": run["previous_version"], "status": "observed", "trigger": "provider_receipt",
                        "receipt_digest": receipt["digest"], "auto": False}
            deployed_version = run["previous_version"]
        elif online:
            add("feedback", "passed", online.get("at"), msg(lang, "pipeline.feedback.collected"),
                metrics={k: online.get(k) for k in ("thumbs_down_ratio", "faithfulness_drop", "error_rate") if k in online})
            breach = check_rollback(online, rollback_cfg)
            if breach:
                rollback = {
                    "at": online.get("at"),
                    "usecase": run.get("usecase"),
                    "from": run.get("version"),
                    "to": run.get("previous_version"),
                    "trigger": breach["trigger"],
                    "value": breach["value"],
                    "threshold": breach["threshold"],
                    "auto": not verified,
                }
                if verified:
                    rollback["status"] = "recommended"
                else:
                    deployed_version = run.get("previous_version")
                add("rollback_guard", "failed", online.get("at"),
                    None if verified else msg(lang, "pipeline.rollback.auto", to=run.get("previous_version"),
                        trigger=trigger_label(breach["trigger"], lang), value=breach["value"],
                        threshold=breach["threshold"]),
                    trigger=breach["trigger"])
            else:
                add("rollback_guard", "passed", online.get("at"), msg(lang, "pipeline.rollback.ok"))
        else:
            add("feedback", "running", None, msg(lang, "pipeline.feedback.collecting"))
            add("rollback_guard", "running", None, msg(lang, "pipeline.rollback.monitoring"))
    else:
        add("feedback", "pending")
        add("rollback_guard", "pending" if blocked_by is None else "skipped")

    item = {
        "usecase": run.get("usecase"),
        "version": run.get("version"),
        "previous_version": run.get("previous_version"),
        "deployed_version": deployed_version,
        "prompt_version": run.get("prompt_version"),
        "agent_version": run.get("agent_version"),
        "model_version": model_version,
        "risk_tier": tier,
        "stages": out_stages,
        "blocked_by": blocked_by,
        "gate_passed": gate_passed,
    }
    if verified:
        item.update({"source": run["source"], "observed_at": run["observed_at"], "run_id": run["run_id"],
                     "review_revision": run["review_revision"], "artifact_digest": run["artifact_digest"],
                     "deployment_receipt": run.get("deployment_receipt"),
                     "deployment_observed": bool(run.get("deployment_receipt"))})
    return item, rollback


def evaluate_pipeline(runs: list[dict[str, Any]], registry: list[dict[str, Any]], factory_cfg: dict[str, Any],
                      history: list[dict[str, Any]] | None = None, lang: str = DEFAULT_LANG, *,
                      verified: bool = False) -> dict[str, Any]:
    risk_by_uc = {}
    for item in registry:
        risk = dict(item.get("risk") or {})
        risk["reviewed_model_version"] = (item.get("assessment") or {}).get("reviewed_model_version")
        if verified:
            risk["assessment"] = item.get("assessment") or {}
        risk_by_uc[item.get("id")] = risk
    eval_gates = factory_cfg.get("eval_gates") or {}
    rollback_cfg = factory_cfg.get("rollback") or {}
    items = []
    rollbacks = [dict(r) for r in history or []]
    for run in runs or []:
        risk = risk_by_uc.get(run.get("usecase"))
        if risk is None:
            continue  # unknown use case: ignore rather than invent a risk tier
        item, rb = evaluate_run(run, risk, eval_gates, rollback_cfg, lang, verified=verified)
        items.append(item)
        if rb:
            rollbacks.append(rb)
    for rb in rollbacks:
        rb["trigger_label"] = trigger_label(rb.get("trigger"), lang)
    rollbacks.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
    summary = {
        "items": len(items),
        "gate_passed": sum(1 for i in items if i["gate_passed"]),
        "blocked": sum(1 for i in items if i["blocked_by"]),
        "blocked_risk": sum(1 for i in items if i["blocked_by"] == "risk_reassessment"),
        "rollbacks": len(rollbacks),
    }
    return {"stages": list(STAGES), "items": items, "rollbacks": rollbacks,
            "thresholds": eval_gates, "rollback_rules": rollback_cfg, "summary": summary}
