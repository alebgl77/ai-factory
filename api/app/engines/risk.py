"""Risk engine: AI Act tier, applicable date, re-evaluation flag, controls, NIST progress.

Pure functions on plain dicts (CONTRACT section 2.4). Indicative, not legal advice.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..i18n import DEFAULT_LANG, msg
from ..util import to_date

TIERS = ("prohibited", "high", "limited", "minimal")
DECISIONS = ("approved", "approved_with_conditions", "rejected", "pending")
NIST_FUNCTIONS = ("govern", "map", "measure", "manage")
_NIST_SCORE = {"done": 1.0, "partial": 0.5}


def compute_tier(ai_act: dict[str, Any] | None) -> str:
    a = ai_act or {}
    if a.get("prohibited_practices"):
        return "prohibited"
    annex_iii = bool(a.get("annex_iii_area")) and not bool(a.get("art6_3_exemption"))
    if annex_iii or bool(a.get("annex_i_safety_component")):
        return "high"
    if a.get("transparency"):
        return "limited"
    return "minimal"


def _milestones(regulatory: dict[str, Any] | None) -> dict[str, dt.date]:
    out: dict[str, dt.date] = {}
    ai_act = (regulatory or {}).get("ai_act") or {}
    for m in ai_act.get("milestones") or []:
        if isinstance(m, dict) and m.get("id"):
            d = to_date(m.get("date"))
            if d:
                out[str(m["id"])] = d
    return out


def governing_milestone(tier: str, ai_act: dict[str, Any] | None) -> list[str]:
    """Milestone ids that govern a tier (the earliest applicable one wins)."""
    a = ai_act or {}
    if tier == "prohibited":
        return ["prohibitions"]
    if tier == "high":
        ids = []
        if a.get("annex_iii_area") and not a.get("art6_3_exemption"):
            ids.append("annex_iii")
        if a.get("annex_i_safety_component"):
            ids.append("annex_i")
        return ids or ["annex_iii"]
    if tier == "limited":
        return ["transparency"]
    return ["literacy"]


def applicable_from(tier: str, ai_act: dict[str, Any] | None, regulatory: dict[str, Any] | None,
                    today: dt.date) -> tuple[str | None, int | None, str | None]:
    milestones = _milestones(regulatory)
    candidates = [(milestones[m], m) for m in governing_milestone(tier, ai_act) if m in milestones]
    if not candidates:
        return None, None, None
    date, mid = min(candidates)
    return date.isoformat(), (date - today).days, mid


def tier_label(tier: str | None, lang: str = DEFAULT_LANG) -> str:
    """Human-readable AI Act tier (the ``tier`` field itself stays an enum id)."""
    return msg(lang, f"risk.tier.{tier}") if tier in TIERS else str(tier)


def reevaluation(uc: dict[str, Any], lang: str = DEFAULT_LANG) -> tuple[bool, str | None]:
    model = uc.get("model") or {}
    assessment = uc.get("assessment") or {}
    version = model.get("version")
    reviewed = assessment.get("reviewed_model_version")
    decision = assessment.get("decision")
    reasons = []
    if version is not None and str(version) != str(reviewed):
        last = reviewed if reviewed is not None else msg(lang, "risk.reason.no_review")
        reasons.append(msg(lang, "risk.reason.model_not_reviewed", version=str(version), last=str(last)))
    if decision == "pending" or decision is None:
        reasons.append(msg(lang, "risk.reason.assessment_pending"))
    if not reasons:
        return False, None
    return True, msg(lang, "risk.reason.separator").join(reasons)


def nist_status(uc: dict[str, Any]) -> dict[str, str]:
    nist = uc.get("nist") or {}
    out = {}
    for fn in NIST_FUNCTIONS:
        entry = nist.get(fn)
        status = entry.get("status") if isinstance(entry, dict) else entry
        out[fn] = status if status in ("done", "partial", "todo") else "todo"
    return out


def nist_progress(uc: dict[str, Any]) -> float:
    status = nist_status(uc)
    return round(sum(_NIST_SCORE.get(s, 0.0) for s in status.values()) / len(NIST_FUNCTIONS), 2)


def required_controls(tier: str, gpai: bool, controls: list[dict[str, Any]]) -> list[str]:
    out = []
    for c in controls or []:
        tiers = c.get("tiers") or []
        if tier in tiers or (gpai and "gpai" in tiers):
            out.append(str(c["id"]))
    return out


def controls_status(required: list[str], controls: list[dict[str, Any]], iso_implemented: list[str]) -> list[dict[str, Any]]:
    """A required control is considered evidenced when one of its ISO/IEC 42001 refs is implemented."""
    by_id = {str(c.get("id")): c for c in controls or []}
    implemented = {str(x) for x in iso_implemented or []}
    out = []
    for cid in required:
        c = by_id.get(cid, {})
        refs = [str(x) for x in c.get("iso42001") or []]
        out.append({
            "id": cid,
            "title": c.get("title"),
            "implemented": bool(implemented.intersection(refs)),
            "ai_act": c.get("ai_act") or [],
            "nist": c.get("nist") or [],
            "iso42001": refs,
        })
    return out


def evaluate_risk(uc: dict[str, Any], regulatory: dict[str, Any] | None, controls: list[dict[str, Any]],
                  today: dt.date, lang: str = DEFAULT_LANG) -> dict[str, Any]:
    ai_act = uc.get("ai_act") or {}
    tier = compute_tier(ai_act)
    gpai = bool(ai_act.get("gpai_used"))
    date, days_left, milestone = applicable_from(tier, ai_act, regulatory, today)
    reeval, reason = reevaluation(uc, lang)
    decision = (uc.get("assessment") or {}).get("decision")
    req = required_controls(tier, gpai, controls)
    status = controls_status(req, controls, uc.get("iso42001") or [])
    deploy_block = None
    if tier == "prohibited":
        deploy_block = "prohibited_practice"
    elif reeval:
        deploy_block = "risk_reassessment"
    elif decision == "rejected":
        deploy_block = "assessment_rejected"
    return {
        "tier": tier,
        "gpai": gpai,
        "role": ai_act.get("role"),
        "annex_iii_area": ai_act.get("annex_iii_area"),
        "annex_iii_use": ai_act.get("annex_iii_use"),
        "art6_3_exemption": bool(ai_act.get("art6_3_exemption")),
        "annex_i_safety_component": bool(ai_act.get("annex_i_safety_component")),
        "prohibited_practices": list(ai_act.get("prohibited_practices") or []),
        "transparency": list(ai_act.get("transparency") or []),
        "applicable_from": date,
        "applicable_milestone": milestone,
        "days_left": days_left,
        "in_force": days_left is not None and days_left <= 0,
        "reevaluation_required": reeval,
        "reason": reason,
        "decision": decision,
        "deploy_block": deploy_block,
        "required_controls": req,
        "controls_status": status,
        "controls_coverage": round(sum(1 for c in status if c["implemented"]) / len(status), 2) if status else 1.0,
        "nist_progress": nist_progress(uc),
        "nist": nist_status(uc),
        "iso42001": list(uc.get("iso42001") or []),
    }


def squad_of(uc: dict[str, Any], people: list[dict[str, Any]]) -> list[str]:
    uc_id = uc.get("id")
    members = [str(p["id"]) for p in people if p.get("id") and uc_id in (p.get("squads") or [])]
    owner = uc.get("owner")
    if owner and owner not in members:
        members.insert(0, str(owner))
    return members


def evaluate_registry(usecases: list[dict[str, Any]], factory_cfg: dict[str, Any], controls: list[dict[str, Any]],
                      people: list[dict[str, Any]], today: dt.date, lang: str = DEFAULT_LANG) -> list[dict[str, Any]]:
    """Evaluate every use case. Text fields must already be localised (``i18n.localize_tree``)."""
    regulatory = factory_cfg.get("regulatory") or {}
    sensitive_classes = set((factory_cfg.get("placement") or {}).get("sensitive_classes") or [])
    out = []
    for uc in usecases:
        model = uc.get("model") or {}
        out.append({
            "id": uc.get("id"),
            "code": uc.get("code") or str(uc.get("id", "")).upper(),
            "name": uc.get("name"),
            "description": uc.get("description"),
            "stage": uc.get("stage"),
            "sensitivity": uc.get("sensitivity"),
            "sensitive": uc.get("sensitivity") in sensitive_classes,
            "color": uc.get("color"),
            "owner": uc.get("owner"),
            "cost_center": uc.get("cost_center"),
            "task_type": uc.get("task_type"),
            "risk": evaluate_risk(uc, regulatory, controls, today, lang),
            "model": {
                "alias": model.get("alias"),
                "name": model.get("name"),
                "version": model.get("version"),
                "history": list(model.get("history") or []),
            },
            "assessment": dict(uc.get("assessment") or {}),
            "squad": squad_of(uc, people),
            "kpis": dict(uc.get("kpis") or {}),
            "rag": dict(uc.get("rag") or {}) if uc.get("rag") else None,
            "ai_act": dict(uc.get("ai_act") or {}),
        })
    return out


def tier_counts(registry: list[dict[str, Any]]) -> dict[str, int]:
    counts = {t: 0 for t in TIERS}
    for item in registry:
        counts[item["risk"]["tier"]] = counts.get(item["risk"]["tier"], 0) + 1
    return counts
