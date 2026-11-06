"""Hybrid placement: current decisions and what-if decisions."""

from __future__ import annotations

import copy
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..engines.placement import decide
from ..i18n import msg
from ..security import PLACEMENT_DECIDE, Access, require
from ..service import FactoryService
from ..state import StateError
from .deps import ID_PATTERN, Reader, not_found, response_lang, service, state_unavailable

router = APIRouter(prefix="/api/v1/placement", tags=["placement"])

Status = Literal["up", "degraded", "down"]


class TargetsOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cloud: Status | None = None
    vps: Status | None = None
    local: Status | None = None


class PlacementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    usecase: str | None = Field(default=None, pattern=ID_PATTERN)
    sensitivity: Literal["C1", "C2", "C3", "C4"] | None = None
    task_type: Literal["rag_qa", "agent", "classification", "extraction", "routing", "generation",
                       "summarization"] | None = None
    params_b: float | None = Field(default=None, ge=0, le=5000)
    model_alias: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    targets: TargetsOverride | None = None


@router.get("", summary="Hybrid placement: cloud / VPS / local")
def get_placement(access: Reader, svc: FactoryService = Depends(service),
                  lang: str = Depends(response_lang)) -> dict[str, Any]:
    return svc.snapshot(lang=lang, scope=svc.business_scope(access))["placement"]


@router.post("/decide", summary="Placement decision (outages can be simulated); role editor")
def post_decide(body: PlacementIn, access: Access = Depends(require(PLACEMENT_DECIDE, write=True)),
                svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    """What-if decision; ``?lang=`` only selects the language of the reasons. No state is written,
    but the decision is audited (inputs and decision hashes, target, sensitivity)."""
    scope = svc.business_scope(access)
    if scope is not None and not body.usecase:
        raise HTTPException(status_code=422, detail=msg(lang, "error.usecase_not_found"))
    if body.usecase:
        case, access = svc.scoped_case(body.usecase, access)
        if case is None:
            raise not_found(lang)
    snap = svc.snapshot(lang=lang, scope=scope)
    placement = snap["placement"]
    workload: dict[str, Any] = {"id": "wl-what-if"}
    if body.usecase:
        item = next((r for r in snap["registry"] if r["id"] == body.usecase), None)
        if item is None:
            raise not_found(lang)
        current = next((w for w in placement["workloads"] if w["usecase"] == body.usecase), {})
        workload.update({"usecase": item["id"], "sensitivity": item["sensitivity"], "task_type": item["task_type"],
                         "model_alias": item["model"]["alias"], "params_b": current.get("params_b")})
    for key in ("sensitivity", "task_type", "params_b", "model_alias"):
        value = getattr(body, key)
        if value is not None:
            if key == "sensitivity" and scope is not None and value < workload["sensitivity"]:
                raise HTTPException(status_code=403, detail=msg(lang, "auth.forbidden"))
            workload[key] = value
    if not workload.get("sensitivity"):
        raise HTTPException(status_code=422, detail=msg(lang, "error.sensitivity_required"))
    targets = copy.deepcopy(placement["targets"])
    if body.targets:
        overrides = body.targets.model_dump(exclude_none=True)
        for t in targets:
            if t["id"] in overrides:
                t["status"] = overrides[t["id"]]
    policy = svc.localized_config(lang).get("factory", {}).get("placement") or {}
    decision = decide(workload, targets, policy, lang)
    inputs = {"workload": {k: workload.get(k) for k in ("usecase", "sensitivity", "task_type", "params_b",
                                                         "model_alias")},
              "targets": {t["id"]: t["status"] for t in targets}}
    try:
        svc.state.append_audit(access.audit_context(), action="placement.decide",
                               target=f"placement:{workload.get('usecase') or 'what-if'}", before=inputs,
                               after={"target": decision.get("target"), "status": decision.get("status"),
                                      "model_alias": decision.get("model_alias")},
                               details={"usecase": workload.get("usecase"), "sensitivity": workload.get("sensitivity"),
                                        "target": decision.get("target"), "simulated": bool(body.targets)})
    except StateError:
        raise state_unavailable(lang, audit=True) from None
    return {"decision": decision, "targets": [{"id": t["id"], "status": t["status"]} for t in targets],
            "simulated": bool(body.targets)}
