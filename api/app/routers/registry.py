"""Use case registry: evaluated risk, model-version changes and risk assessments (CONTRACT 2.4)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field

from ..i18n import msg
from ..service import FactoryService
from ..state import StateError
from .deps import ID_PATTERN, not_found, response_lang, service

router = APIRouter(prefix="/api/v1/registry", tags=["registry"])


class ModelVersionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:+-]*$")
    name: str | None = Field(default=None, min_length=1, max_length=120)


class AssessmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approved", "approved_with_conditions", "rejected", "pending"]
    reviewer: str = Field(min_length=1, max_length=64, pattern=ID_PATTERN)


# Writes are language-independent (CONTRACT section 9): the state keeps ids, versions, dates and
# decisions only; ``?lang=`` selects the language of the texts of the answer (reason, message).


@router.get("", summary="Registry of evaluated use cases")
def list_registry(svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> list[dict[str, Any]]:
    return svc.snapshot(lang=lang)["registry"]


@router.get("/{uc_id}", summary="Use case record")
def get_registry(uc_id: str = Path(pattern=ID_PATTERN), svc: FactoryService = Depends(service),
                 lang: str = Depends(response_lang)) -> dict[str, Any]:
    snap = svc.snapshot(lang=lang)
    for item in snap["registry"]:
        if item["id"] == uc_id:
            pipeline = next((p for p in snap["pipeline"]["items"] if p["usecase"] == uc_id), None)
            costs = next((c for c in snap["costs"]["by_usecase"] if c["usecase"] == uc_id), None)
            return {**item, "pipeline": pipeline, "costs": costs}
    raise not_found(lang)


@router.post("/{uc_id}/model-version", summary="New model version (triggers the risk reassessment)")
def post_model_version(body: ModelVersionIn, uc_id: str = Path(pattern=ID_PATTERN),
                       svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    uc = svc.find_usecase(uc_id)
    if uc is None:
        raise not_found(lang)
    if str((uc.get("model") or {}).get("version")) == body.version:
        raise HTTPException(status_code=409, detail=msg(lang, "error.version_is_current"))
    try:
        svc.state.set_model_version(uc, body.version, body.name, svc.today())
    except StateError:
        raise HTTPException(status_code=503, detail=msg(lang, "error.state_unavailable")) from None
    svc.invalidate()
    item = svc.evaluate_usecase(svc.find_usecase(uc_id) or uc, lang)
    return {"usecase": uc_id, "model": item["model"], "assessment": item["assessment"],
            "reevaluation_required": item["risk"]["reevaluation_required"], "reason": item["risk"]["reason"],
            "deploy_gate": "blocked" if item["risk"]["deploy_block"] else "open",
            "message": msg(lang, "registry.model_version_recorded"),
            "risk": item["risk"]}


@router.post("/{uc_id}/assessment", summary="Record a risk review of the current model version")
def post_assessment(body: AssessmentIn, uc_id: str = Path(pattern=ID_PATTERN),
                    svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    uc = svc.find_usecase(uc_id)
    if uc is None:
        raise not_found(lang)
    people = {str(p.get("id")) for p in (svc.config.get().get("profiles") or {}).get("people") or []}
    if people and body.reviewer not in people:
        raise HTTPException(status_code=422, detail=msg(lang, "error.unknown_reviewer"))
    try:
        svc.state.record_assessment(uc, body.decision, body.reviewer, svc.today())
    except StateError:
        raise HTTPException(status_code=503, detail=msg(lang, "error.state_unavailable")) from None
    svc.invalidate()
    item = svc.evaluate_usecase(svc.find_usecase(uc_id) or uc, lang)
    return {"usecase": uc_id, "assessment": item["assessment"],
            "reevaluation_required": item["risk"]["reevaluation_required"], "reason": item["risk"]["reason"],
            "deploy_gate": "blocked" if item["risk"]["deploy_block"] else "open", "risk": item["risk"]}
