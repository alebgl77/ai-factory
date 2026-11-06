"""Authenticated BU-scoped ingress for immutable operational observations."""

from __future__ import annotations

import datetime as dt
import hmac
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from ..operating_evidence import EvidenceError, Observation, Release, Signal, validate_release
from ..security import READ, Access, require
from .shadow_ai import ObservatoryRoute

router = APIRouter(prefix="/api/v1/operating-evidence", tags=["operating-evidence"], route_class=ObservatoryRoute)
Writer = Annotated[Access, Depends(require(READ, write=True))]


def _assert_receipt_approval(observation: Release, effective: dict, reporter_ref: str | None) -> None:
    approval = effective.get("_review_approval") or {}
    approver_ref = approval.get("subject_ref")
    try:
        reviewed_at = dt.datetime.fromisoformat(approval.get("reviewed_at"))
    except (TypeError, ValueError):
        raise EvidenceError(409, "release_not_approved") from None
    if (reviewed_at.tzinfo != dt.timezone.utc or not isinstance(approver_ref, str)
            or not approver_ref.isascii() or len(approver_ref) != 64
            or approval.get("revision") != observation.review_revision):
        raise EvidenceError(409, "release_not_approved")
    if not reporter_ref or hmac.compare_digest(approver_ref, reporter_ref):
        raise EvidenceError(403, "separation_of_duties")
    if dt.datetime.fromisoformat(observation.deployment_receipt.at) <= reviewed_at:
        raise EvidenceError(409, "receipt_before_approval")


def _record(observation: Observation, request: Request, access: Access) -> dict[str, str]:
    svc = request.app.state.service
    store = svc.operating_evidence
    principal = access.principal
    groups = frozenset(group for group, role in svc.guard.config.role_map
                       if role == "viewer" and group in store.sources.get(observation.source, ())
                       and group in principal.groups)
    if not principal.authenticated or not principal.user or principal.roles != {"viewer"} or not groups:
        raise HTTPException(403, detail="evidence_source_forbidden")
    scope = svc.business_scope(access, groups=groups)
    if observation.usecase not in svc.scope_case_ids(scope):
        raise HTTPException(404, detail="usecase_not_found")
    effective = next((item for item in svc.effective_usecases() if item.get("id") == observation.usecase), None)
    if effective is None:
        raise HTTPException(404, detail="usecase_not_found")
    if (effective.get("review_revision") != observation.review_revision
            or (effective.get("model") or {}).get("version") != observation.model_version):
        raise HTTPException(409, detail="stale_review_revision")
    try:
        if isinstance(observation, Release) and observation.deployment_receipt is not None:
            _assert_receipt_approval(observation, effective, svc.reviewer_subject_ref(principal))
            item = svc.evaluate_usecase(effective, svc.settings.default_lang)
            validate_release(observation, item, svc.config.get().get("factory") or {})
        stored = store.record(observation, now=dt.datetime.now(dt.timezone.utc), publisher=principal.user)
    except EvidenceError as exc:
        raise HTTPException(exc.status, detail=str(exc)) from None
    except sqlite3.Error:
        raise HTTPException(503, detail="operating_evidence_unavailable") from None
    return {"status": "stored" if stored else "duplicate"}


@router.post("/releases")
def observe_release(observation: Release, request: Request, access: Writer) -> dict[str, str]:
    return _record(observation, request, access)


@router.post("/signals")
def observe_signal(observation: Signal, request: Request, access: Writer) -> dict[str, str]:
    return _record(observation, request, access)
