"""Mobile workbench API, available only in explicitly enabled sandbox modes."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..security import READ, Access, require
from ..workbench.service import Workbench
from ..workbench.store import WorkbenchError

CaseID = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
Comment = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
RequiredText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Reader = Annotated[Access, Depends(require(READ))]
Writer = Annotated[Access, Depends(require(READ, write=True))]


def _origin(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username is not None
                or parsed.path or parsed.query or parsed.fragment):
            return None
        return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def _check_browser_write(request: Request) -> None:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type not in {"application/json", "application/cloudevents+json"}:
        raise HTTPException(415, detail="json_required")
    if request.headers.get("sec-fetch-site", "same-origin") != "same-origin":
        raise HTTPException(403, detail="cross_origin_write")
    origins = request.headers.getlist("origin")
    if not origins:  # explicit JSON clients such as a CLI do not send an Origin
        return
    scheme = request.url.scheme
    # Caddy terminates TLS. Use its forwarded scheme only on the trusted identity hop.
    peer = request.client.host if request.client else None
    if request.app.state.service.guard.config.trusts(peer):
        scheme = request.headers.get("x-forwarded-proto", scheme)
    expected = _origin(f"{scheme}://{request.url.netloc}")
    if len(origins) != 1 or expected is None or _origin(origins[0]) != expected:
        raise HTTPException(403, detail="cross_origin_write")


class WorkbenchRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request):
            if request.method == "POST":
                _check_browser_write(request)
            try:
                return await handler(request)
            except RequestValidationError:
                # FastAPI's default error includes input values (including chat text).
                raise HTTPException(422, detail="invalid_request") from None
            except WorkbenchError as exc:
                raise HTTPException(exc.status, detail=str(exc)) from None
            except (sqlite3.Error, OSError):
                raise HTTPException(503, detail="workbench_unavailable") from None

        return handle


router = APIRouter(prefix="/api/v1/workbench", tags=["workbench"], route_class=WorkbenchRoute)


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["approve", "reject", "request_changes"]
    revision: int = Field(ge=1, le=2**53 - 1)
    proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rationale: RequiredText


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    rating: Literal["helpful", "needs_work"]
    comment: Comment = ""


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    agent: Literal["operations", "finance", "governance"]
    message: RequiredText
    case_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


def _workbench(request: Request) -> Workbench:
    return request.app.state.workbench


@router.get("/cases")
def cases(request: Request, access: Reader):
    workbench = _workbench(request)
    capabilities = workbench.capabilities(access.principal)
    workbench.seed()
    return {"mode": workbench.mode, "cases": workbench.cases(access.principal), "capabilities": capabilities}


@router.post("/events")
def submit(request: Request, event: dict[str, Any], access: Writer):
    return _workbench(request).submit(event, access.principal)


@router.get("/cases/{case_id}/history")
def history(request: Request, case_id: CaseID, access: Reader):
    return _workbench(request).history(case_id, access.principal)


@router.post("/cases/{case_id}/decisions")
def decide(request: Request, case_id: CaseID, decision: DecisionIn, access: Writer):
    return _workbench(request).decide(case_id, access.principal, **decision.model_dump())


@router.post("/cases/{case_id}/feedback")
def feedback(request: Request, case_id: CaseID, feedback: FeedbackIn, access: Writer):
    _workbench(request).feedback(case_id, access.principal, **feedback.model_dump())
    return {"recorded": True}


@router.post("/chat")
def chat(request: Request, chat: ChatIn, access: Writer):
    return _workbench(request).chat(access.principal, **chat.model_dump())
