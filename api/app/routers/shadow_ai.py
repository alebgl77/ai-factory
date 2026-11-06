"""Same-origin protected API for the optional Shadow AI Observatory."""

from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from ..security import READ, Access, require
from ..shadow_ai import MAX_PERIOD_ROWS, ObservatoryError

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)]
Period = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-(?:0[1-9]|1[0-2])$", max_length=7)]
Timestamp = Annotated[str, StringConstraints(
    pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|\+00:00)$",
    max_length=32,
)]
Count = Annotated[int, Field(ge=0, le=10**9)]
ProposalID = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$", max_length=32)]
Status = Literal["reviewing", "pilot", "approved", "restricted"]
Reader = Annotated[Access, Depends(require(READ))]
Writer = Annotated[Access, Depends(require(READ, write=True))]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EvidenceIn(Input):
    window_start: Timestamp
    window_end: Timestamp
    events: Count
    unique_users: Count

    @field_validator("window_start", "window_end")
    @classmethod
    def valid_timestamp(cls, value: str) -> str:
        dt.datetime.fromisoformat(value)
        return value


class TeamUsageIn(Input):
    team: Identifier
    tool: Identifier
    kind: Literal["inventory", "observation", "usage"]
    people: Count
    events: Count


class AdoptionIn(Input):
    team: Identifier
    tool: Identifier
    active: Count
    eligible: Count


class PeriodIn(Input):
    period: Period
    team_usage: list[TeamUsageIn] = Field(max_length=MAX_PERIOD_ROWS)
    adoption: list[AdoptionIn] = Field(max_length=MAX_PERIOD_ROWS)


class ProposalIn(Input):
    tool: Identifier
    status: Status
    alternative: Identifier | None
    expected_revision: Count


class DecisionIn(Input):
    decision: Literal["approve", "reject"]


def _origin(url: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username is not None
                or parsed.path or parsed.query or parsed.fragment):
            return None
        return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def _check_origin(request: Request) -> None:
    fetch_sites = request.headers.getlist("sec-fetch-site")
    if fetch_sites and fetch_sites != ["same-origin"]:
        raise HTTPException(403, detail="cross_origin_request")
    origins = request.headers.getlist("origin")
    if not origins:
        return  # an explicit client, such as an operator CLI, need not send Origin
    scheme = request.url.scheme
    peer = request.client.host if request.client else None
    if request.app.state.service.guard.config.trusts(peer):
        forwarded = request.headers.getlist("x-forwarded-proto")
        if forwarded:
            scheme = forwarded[0] if len(forwarded) == 1 else ""
    expected = _origin(f"{scheme}://{request.url.netloc}")
    if len(origins) != 1 or expected is None or _origin(origins[0]) != expected:
        raise HTTPException(403, detail="cross_origin_request")


def _observatory_roles(
    request: Request, access: Access, required: frozenset[str] | None = None,
) -> frozenset[str]:
    principal = access.principal
    allowed = request.app.state.settings.shadow_ai_groups
    assigned_groups = set(allowed).intersection(principal.groups)
    role_map = request.app.state.service.guard.config.role_map
    roles = frozenset(role for group, role in role_map if group in assigned_groups)
    if not principal.authenticated or not roles or (required and (not principal.user or not roles & required)):
        raise HTTPException(403, detail="observatory_forbidden")
    return roles


class ObservatoryRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request):
            _check_origin(request)
            if request.method == "POST":
                media_types = request.headers.getlist("content-type")
                if len(media_types) != 1 or media_types[0].split(";", 1)[0].strip().lower() != "application/json":
                    raise HTTPException(415, detail="json_required")
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, detail="invalid_request") from None
            except ObservatoryError as exc:
                raise HTTPException(exc.status, detail=str(exc)) from None
            except (OSError, sqlite3.Error):
                raise HTTPException(503, detail="observatory_unavailable") from None

        return handle


router = APIRouter(prefix="/api/v1/shadow-ai", tags=["shadow-ai"], route_class=ObservatoryRoute)
EDITORS = frozenset({"editor", "admin"})
ADMINS = frozenset({"admin"})
APPROVERS = frozenset({"approver"})


@router.get("")
def observatory(request: Request, access: Reader):
    roles = _observatory_roles(request, access)
    if not access.principal.user:
        roles = frozenset()
    return request.app.state.shadow_ai.read(propose=bool(roles & EDITORS), decide=bool(roles & APPROVERS))


@router.post("/evidence")
def import_evidence(payload: EvidenceIn, request: Request, access: Writer):
    _observatory_roles(request, access, ADMINS)
    request.app.state.shadow_ai.import_evidence(
        dt.datetime.fromisoformat(payload.window_start), dt.datetime.fromisoformat(payload.window_end),
        payload.events, payload.unique_users, access.principal,
    )
    return {"status": "stored"}


@router.post("/periods")
def import_period(payload: PeriodIn, request: Request, access: Writer):
    _observatory_roles(request, access, ADMINS)
    request.app.state.shadow_ai.import_period(
        payload.period, [row.model_dump() for row in payload.team_usage],
        [row.model_dump() for row in payload.adoption], access.principal,
    )
    return {"status": "stored"}


@router.post("/policies/proposals", status_code=201)
def propose_policy(payload: ProposalIn, request: Request, access: Writer):
    _observatory_roles(request, access, EDITORS)
    proposal_id = request.app.state.shadow_ai.propose(payload.model_dump(), access.principal)
    return {"id": proposal_id}


@router.post("/policies/proposals/{proposal_id}/decision")
def decide_policy(proposal_id: ProposalID, payload: DecisionIn, request: Request, access: Writer):
    _observatory_roles(request, access, APPROVERS)
    request.app.state.shadow_ai.decide(proposal_id, payload.decision, access.principal)
    return {"status": payload.decision}
