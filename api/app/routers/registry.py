"""Use case registry: evaluated risk, model-version changes and risk assessments (CONTRACT 2.4)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from ..i18n import msg
from ..security import ASSESSMENT, MODEL_VERSION, Access, require
from ..service import FactoryService
from ..state import AuditError, SeparationOfDuties, StateError
from .deps import ID_PATTERN, READ_ONLY, not_found, response_lang, service, state_unavailable

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
# Every write requires its own permission, is rate limited and appends a hash-chained audit record.


@router.get("", dependencies=READ_ONLY, summary="Registry of evaluated use cases")
def list_registry(svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> list[dict[str, Any]]:
    return svc.snapshot(lang=lang)["registry"]


@router.get("/{uc_id}", dependencies=READ_ONLY, summary="Use case record")
def get_registry(uc_id: str = Path(pattern=ID_PATTERN), svc: FactoryService = Depends(service),
                 lang: str = Depends(response_lang)) -> dict[str, Any]:
    snap = svc.snapshot(lang=lang)
    for item in snap["registry"]:
        if item["id"] == uc_id:
            pipeline = next((p for p in snap["pipeline"]["items"] if p["usecase"] == uc_id), None)
            costs = next((c for c in snap["costs"]["by_usecase"] if c["usecase"] == uc_id), None)
            return {**item, "pipeline": pipeline, "costs": costs}
    raise not_found(lang)


@router.post("/{uc_id}/model-version", summary="New model version (triggers the risk reassessment); role maintainer")
def post_model_version(body: ModelVersionIn, uc_id: str = Path(pattern=ID_PATTERN),
                       access: Access = Depends(require(MODEL_VERSION, write=True)),
                       svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    uc = svc.find_usecase(uc_id)
    if uc is None:
        raise not_found(lang)
    if str((uc.get("model") or {}).get("version")) == body.version:
        raise HTTPException(status_code=409, detail=msg(lang, "error.version_is_current"))
    try:
        svc.state.set_model_version(uc, body.version, body.name, svc.today(), audit=access.audit_context())
    except StateError as exc:
        raise state_unavailable(lang, isinstance(exc, AuditError)) from None
    svc.invalidate()
    item = svc.evaluate_usecase(svc.find_usecase(uc_id) or uc, lang)
    return {"usecase": uc_id, "model": item["model"], "assessment": item["assessment"],
            "reevaluation_required": item["risk"]["reevaluation_required"], "reason": item["risk"]["reason"],
            "deploy_gate": "blocked" if item["risk"]["deploy_block"] else "open",
            "message": msg(lang, "registry.model_version_recorded"),
            "risk": item["risk"]}


@router.post("/{uc_id}/assessment",
             summary="Record a risk review of the current model version; role approver, four-eyes rule")
def post_assessment(request: Request, body: AssessmentIn, uc_id: str = Path(pattern=ID_PATTERN),
                    access: Access = Depends(require(ASSESSMENT, write=True)),
                    svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    """Separation of duties (AI Act record-keeping, ISO/IEC 42001): when authentication is on, an
    approving decision by the identity that recorded the current model version is refused with 403
    ``separation_of_duties`` (and audited as ``denied``). The authenticated reviewer is recorded in
    the assessment (``reviewed_by``, ``reviewed_by_role``, ``separation_of_duties``)."""
    uc = svc.find_usecase(uc_id)
    if uc is None:
        raise not_found(lang)
    people = {str(p.get("id")) for p in (svc.config.get().get("profiles") or {}).get("people") or []}
    if people and body.reviewer not in people:
        raise HTTPException(status_code=422, detail=msg(lang, "error.unknown_reviewer"))
    try:
        svc.state.record_assessment(uc, body.decision, body.reviewer, svc.today(), audit=access.audit_context(),
                                    enforce_four_eyes=svc.guard.config.enforced)
    except SeparationOfDuties as exc:
        raise svc.guard.deny(request.scope, 403, "separation_of_duties", "auth.four_eyes",
                             principal=access.principal, permission=ASSESSMENT,
                             params={"version": exc.version}) from None
    except StateError as exc:
        raise state_unavailable(lang, isinstance(exc, AuditError)) from None
    svc.invalidate()
    item = svc.evaluate_usecase(svc.find_usecase(uc_id) or uc, lang)
    return {"usecase": uc_id, "assessment": item["assessment"],
            "reevaluation_required": item["risk"]["reevaluation_required"], "reason": item["risk"]["reason"],
            "deploy_gate": "blocked" if item["risk"]["deploy_block"] else "open", "risk": item["risk"]}
