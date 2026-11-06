"""Optional server persistence of the 3D bookings (optimistic concurrency on ``version``)."""

from __future__ import annotations

import json
from functools import partial
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..bu_bookings import BookingAccess, BookingConflict, BookingViewConflict, InvalidBooking, recover_spaces
from ..i18n import msg
from ..security import READ, SPACES_WRITE, Access, SecurityDenied, require
from ..service import FactoryService
from ..state import AuditError, StateError, VersionConflict
from .deps import Reader, response_lang, service, state_unavailable

router = APIRouter(prefix="/api/v1/spaces", tags=["spaces"])
recovery_router = APIRouter(prefix="/api/v1/spaces", tags=["spaces"])
MAX_DEPTH = 8


def _depth(obj: Any, level: int = 0) -> int:
    if isinstance(obj, dict):
        return max([level] + [_depth(v, level + 1) for v in obj.values()])
    if isinstance(obj, list):
        return max([level] + [_depth(v, level + 1) for v in obj])
    return level


class SpacesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0)
    state: dict[str, Any]
    view_token: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("state")
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        if _depth(value) > MAX_DEPTH:
            raise ValueError(f"structure too deep (max {MAX_DEPTH} levels)")
        json.dumps(value)  # must be plain JSON
        return value


def _booking_access(svc: FactoryService, access: Access) -> BookingAccess | None:
    policy = svc.bu_policy
    if policy is None:
        return None
    read = svc.business_scope(access, permission=READ)
    assert read is not None
    write = policy.scope(access, svc.guard.config, permission=SPACES_WRITE)
    cases = {str(case["id"]): case for case in svc.config.get().get("usecases") or []}
    assert svc.booking_key is not None
    return BookingAccess(policy, read, write, cases, access.principal.user, svc.booking_key)


def _without_owners(doc: dict[str, Any]) -> dict[str, Any]:
    return {key: doc.get(key) for key in ("version", "updated_at", "state")}


@router.get("/state", summary="State of the 3D bookings")
def get_state(access: Reader, svc: FactoryService = Depends(service),
              lang: str = Depends(response_lang)) -> dict[str, Any]:
    booking = _booking_access(svc, access)
    doc = svc.state.spaces_state()
    if booking is not None:
        return booking.project(doc)
    if "owners" in doc:
        raise state_unavailable(lang)
    return doc


@router.put("/state", summary="Save the 3D bookings (expected version); role editor")
def put_state(body: SpacesIn, access: Access = Depends(require(SPACES_WRITE, write=True)),
              svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> Any:
    booking = _booking_access(svc, access)
    if booking is None and "view_token" in body.model_fields_set:
        raise HTTPException(status_code=422, detail="invalid_booking")
    try:
        result = svc.state.put_spaces_state(body.version, body.state, audit=access.audit_context(),
                                            merge=partial(booking.merge, view_token=body.view_token) if booking else None)
        return booking.project(result) if booking else result
    except VersionConflict as exc:
        return JSONResponse(status_code=409, content={"detail": msg(lang, "error.version_conflict"),
                                                      "current": booking.project(exc.current) if booking else exc.current})
    except InvalidBooking:
        raise HTTPException(status_code=422, detail="invalid_booking") from None
    except BookingViewConflict:
        raise HTTPException(status_code=409, detail="booking_view_conflict") from None
    except BookingConflict:
        raise HTTPException(status_code=409, detail="booking_conflict") from None
    except StateError as exc:
        raise state_unavailable(lang, isinstance(exc, AuditError)) from None


def _require_recovery(svc: FactoryService, access: Access) -> None:
    scope = svc.business_scope(access)
    if scope is None or not scope.global_admin:
        raise SecurityDenied(403, "business_scope_forbidden", "auth.forbidden")


@recovery_router.get("/recovery", summary="Inspect all planning data for an explicit global-admin recovery")
def get_recovery(access: Reader, svc: FactoryService = Depends(service)) -> dict[str, Any]:
    _require_recovery(svc, access)
    return _without_owners(svc.state.spaces_state())


@recovery_router.put("/recovery", summary="Restore planning data explicitly; global admin only")
def put_recovery(body: SpacesIn, access: Access = Depends(require(SPACES_WRITE, write=True)),
                 svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> Any:
    _require_recovery(svc, access)
    try:
        doc = svc.state.put_spaces_state(body.version, body.state, audit=access.audit_context(), merge=recover_spaces)
        return _without_owners(doc)
    except VersionConflict as exc:
        return JSONResponse(status_code=409, content={"detail": msg(lang, "error.version_conflict"),
                                                      "current": _without_owners(exc.current)})
    except StateError as exc:
        raise state_unavailable(lang, isinstance(exc, AuditError)) from None
