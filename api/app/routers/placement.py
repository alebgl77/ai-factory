"""Hybrid placement: current decisions and what-if decisions."""

from __future__ import annotations

import copy
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..engines.placement import decide
from ..i18n import msg
from ..service import FactoryService
from .deps import ID_PATTERN, not_found, response_lang, service

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
def get_placement(svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    return svc.snapshot(lang=lang)["placement"]


@router.post("/decide", summary="Placement decision (outages can be simulated)")
def post_decide(body: PlacementIn, svc: FactoryService = Depends(service),
                lang: str = Depends(response_lang)) -> dict[str, Any]:
    """What-if decision; ``?lang=`` only selects the language of the reasons (no state is written)."""
    snap = svc.snapshot(lang=lang)
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
    return {"decision": decision, "targets": [{"id": t["id"], "status": t["status"]} for t in targets],
            "simulated": bool(body.targets)}
