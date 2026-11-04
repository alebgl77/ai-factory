"""Optional server persistence of the 3D bookings (optimistic concurrency on ``version``)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..i18n import msg
from ..security import SPACES_WRITE, Access, require
from ..service import FactoryService
from ..state import AuditError, StateError, VersionConflict
from .deps import READ_ONLY, response_lang, service, state_unavailable

router = APIRouter(prefix="/api/v1/spaces", tags=["spaces"])
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

    @field_validator("state")
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        if _depth(value) > MAX_DEPTH:
            raise ValueError(f"structure too deep (max {MAX_DEPTH} levels)")
        json.dumps(value)  # must be plain JSON
        return value


@router.get("/state", dependencies=READ_ONLY, summary="State of the 3D bookings")
def get_state(svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    # The bookings are user data stored as sent: ``?lang=`` is accepted but changes nothing.
    return svc.state.spaces_state()


@router.put("/state", summary="Save the 3D bookings (expected version); role editor")
def put_state(body: SpacesIn, access: Access = Depends(require(SPACES_WRITE, write=True)),
              svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> Any:
    try:
        return svc.state.put_spaces_state(body.version, body.state, audit=access.audit_context())
    except VersionConflict as exc:
        return JSONResponse(status_code=409, content={"detail": msg(lang, "error.version_conflict"),
                                                      "current": exc.current})
    except StateError as exc:
        raise state_unavailable(lang, isinstance(exc, AuditError)) from None
