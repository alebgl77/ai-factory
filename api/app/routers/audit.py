"""Audit trail of the writes (hash-chained, ``app/audit.py``) and the caller's identity.

``GET /api/v1/audit`` and ``GET /api/v1/audit/verify`` require the ``audit.read`` permission
(roles ``approver`` and ``admin``); ``GET /api/v1/whoami`` requires an authenticated identity only.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from ..audit import ACTIONS
from ..security import AUDIT_READ, AUTHENTICATED, Access, require
from ..service import FactoryService
from ..state import StateError
from .deps import response_lang, service, state_unavailable

router = APIRouter(prefix="/api/v1", tags=["security"])

Action = Literal[ACTIONS]  # type: ignore[valid-type]


@router.get("/audit", summary="Audit records of every write, newest first (roles approver, admin)")
def audit_records(offset: int = Query(0, ge=0, le=1_000_000), limit: int = Query(100, ge=1, le=500),
                  action: Action | None = Query(None), _: Access = Depends(require(AUDIT_READ)),
                  svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    try:
        records = svc.state.audit_records()
    except StateError:
        raise state_unavailable(lang, audit=True) from None
    if action:
        records = [r for r in records if r.get("action") == action]
    records.reverse()
    return {"total": len(records), "offset": offset, "limit": limit, "order": "desc",
            "records": records[offset:offset + limit]}


@router.get("/audit/verify", summary="Recompute the audit hash chain and reconcile it with the state")
def audit_verify(_: Access = Depends(require(AUDIT_READ)), svc: FactoryService = Depends(service),
                 lang: str = Depends(response_lang)) -> dict[str, Any]:
    try:
        result = svc.state.verify_audit()
    except StateError:
        raise state_unavailable(lang, audit=True) from None
    svc.invalidate()  # the posture in meta.security follows the fresh verification
    return {**result, "algorithm": "sha256-chain",
            "verified_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


@router.get("/whoami", summary="Identity, roles and permissions of the caller as the API sees them")
def whoami(access: Access = Depends(require(AUTHENTICATED))) -> dict[str, Any]:
    p = access.principal
    return {"actor": p.actor, "authenticated": p.authenticated, "auth_mode": p.auth_mode,
            "roles": sorted(p.roles), "permissions": p.permissions(), "groups": list(p.groups),
            "request_id": access.request_id}
