"""Optional server persistence of the 3D bookings (optimistic concurrency on ``version``)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..i18n import msg
from ..service import FactoryService
from ..state import StateError, VersionConflict
from .deps import response_lang, service

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


@router.get("/state", summary="State of the 3D bookings")
def get_state(svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    # The bookings are user data stored as sent: ``?lang=`` is accepted but changes nothing.
    return svc.state.spaces_state()


@router.put("/state", summary="Save the 3D bookings (expected version)")
def put_state(body: SpacesIn, svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> Any:
    try:
        return svc.state.put_spaces_state(body.version, body.state)
    except VersionConflict as exc:
        return JSONResponse(status_code=409, content={"detail": msg(lang, "error.version_conflict"),
                                                      "current": exc.current})
    except StateError:
        raise HTTPException(status_code=503, detail=msg(lang, "error.state_unavailable")) from None
