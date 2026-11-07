"""Mobile workbench API, available only in explicitly enabled sandbox modes."""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
import uuid
from collections.abc import Iterable
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ai_factory_workbench import Workbench, WorkbenchError
from ai_factory_workbench.approvals import ApprovalRelay, RelayAuthority
from ai_factory_workbench.cyberlog import JournalError, journal_request
from ai_factory_workbench.interface_models import CASE_ID_PATTERN, ReviewInput, SurfaceInput
from ai_factory_workbench.profiles import WorkProfile, get_profile, list_profiles

from ..activity import record_workbench_activity
from ..security import READ, Access, require

CaseID = Annotated[str, Path(pattern=CASE_ID_PATTERN)]
Comment = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
RequiredText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Reader = Annotated[Access, Depends(require(READ))]
Writer = Annotated[Access, Depends(require(READ, write=True))]

_ENCOUNTER_STATUSES = {
    ("waiting_review", None): "waiting",
    ("applying", "approve"): "waiting",
    ("decided", "approve"): "approved",
    ("executed", "approve"): "approved",
    ("rejected", "reject"): "rejected",
    ("changes_requested", "request_changes"): "changes_requested",
}


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
                request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex
                with journal_request(request_id):
                    return await handler(request)
            except RequestValidationError:
                # FastAPI's default error includes input values (including chat text).
                raise HTTPException(422, detail="invalid_request") from None
            except WorkbenchError as exc:
                raise HTTPException(exc.status, detail=str(exc)) from None
            except (sqlite3.Error, OSError, JournalError):
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


class SignedApprovalIn(SurfaceInput):
    envelope: dict[Annotated[str, StringConstraints(max_length=40)],
                   Annotated[str, StringConstraints(min_length=1, max_length=1000)] | int] = Field(max_length=16)
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


def configure_workbench_surfaces(
    app: FastAPI, *, profile: str | WorkProfile = "governance", engine: Workbench | None = None,
    authorities: Iterable[RelayAuthority] = (),
) -> None:
    """Bind an operator-selected engine, profile and signed relay at startup.

    Use with an app whose Workbench routes were explicitly enabled. The injected
    engine keeps the configured HTTP access mode. Relays require proxy auth.
    """
    current = getattr(app.state, "workbench", None)
    if current is None:
        raise ValueError("Enable Workbench routes before configuring their surfaces")
    selected = engine if engine is not None else current
    if selected.mode != current.mode:
        raise ValueError("Injected Workbench must use the configured access mode")
    selected_profile = profile if isinstance(profile, WorkProfile) else get_profile(profile)
    trusted = tuple(authorities)
    if trusted and (selected.mode != "protected_demo" or not app.state.service.guard.config.enforced):
        raise ValueError("Signed relays require protected Workbench mode and proxy authentication")
    relay = ApprovalRelay(selected, selected_profile, trusted) if trusted else None
    app.state.workbench = selected
    app.state.workbench_profile = selected_profile
    app.state.workbench_approval_relay = relay
    app.state.workbench_machine_actor_ids = relay.machine_actor_ids if relay is not None else frozenset()


def _workbench(request: Request) -> Workbench:
    return request.app.state.workbench


def _tracks_activity(workbench: Workbench, access: Access) -> bool:
    return (workbench.mode == "protected_demo" and access.principal.authenticated
            and access.principal.auth_mode == "proxy")


def _record_activity(request: Request, access: Access, case_id: str, started_at: dt.datetime | None,
                     finished_at: dt.datetime, *, agent: str | None = None,
                     completed: bool = True, tokens: dict[str, int] | None = None) -> None:
    try:
        event = {
            "id": uuid.uuid4().hex,
            "actor_type": "agent" if agent else "user",
            "action": "Conseil local Workbench" if agent else "Décision Workbench",
            "started_at": started_at.isoformat() if started_at is not None else None,
            "finished_at": finished_at.isoformat(),
            "status": "completed" if completed else "failed",
            "tokens": tokens,
            "supports": [{"kind": "work", "label": "Dossier Workbench", "ref": case_id}],
        }
        if agent:
            event["actor_id"] = agent
        record_workbench_activity(request, access, event)
    except Exception:
        # Activity is best effort and independent of the governed operation.
        pass


def _profile(request: Request) -> WorkProfile:
    configured = getattr(request.app.state, "workbench_profile", None)
    return configured if configured is not None else get_profile()


def _signed_service_only(request: Request, access: Access) -> bool:
    machine_ids = getattr(request.app.state, "workbench_machine_actor_ids", frozenset())
    ids = access.principal.ids | {access.principal.actor}
    aliases = ids | {alias.lower() for alias in ids if "@" in alias}
    return not machine_ids.isdisjoint(aliases)


def _hide_direct_decision(case: dict[str, Any]) -> None:
    case["can_decide"] = False
    case.pop("decision_rationale", None)


@router.get("/cases")
def cases(request: Request, access: Reader):
    workbench = _workbench(request)
    capabilities = workbench.capabilities(access.principal)
    workbench.seed()
    queue = workbench.cases(access.principal)
    if _signed_service_only(request, access):
        capabilities["can_decide"] = False
        for case in queue:
            _hide_direct_decision(case)
    return {"mode": workbench.mode, "cases": queue, "capabilities": capabilities}


@router.get("/encounters")
def encounters(request: Request, access: Reader,
               case_id: Annotated[str | None, Query(pattern=CASE_ID_PATTERN)] = None):
    workbench = _workbench(request)
    if workbench.mode != "protected_demo":
        raise HTTPException(404, detail="workbench_encounters_disabled")
    if case_id is None:
        queue = workbench.cases(access.principal, limit=100, existing_only=True)
    else:
        if len(request.query_params.getlist("case_id")) != 1:
            raise HTTPException(422, detail="invalid_request")
        try:
            queue = [workbench.get_case(case_id, access.principal, existing_only=True)]
        except WorkbenchError as error:
            if error.status != 404 or str(error) != "case_not_found":
                raise
            queue = []
    projected = []
    for case in queue:
        status = _ENCOUNTER_STATUSES.get((case["status"], case["decision"]))
        encounter_id, revision = case["id"], case["revision"]
        if (status is not None and isinstance(encounter_id, str) and re.fullmatch(CASE_ID_PATTERN, encounter_id)
                and type(revision) is int and 1 <= revision <= 2**53 - 1):
            projected.append({"case_id": encounter_id, "revision": revision, "status": status})
    return {"encounters": projected}


@router.post("/events")
def submit(request: Request, event: dict[str, Any], access: Writer):
    profile = _profile(request).snapshot() if access.principal.authenticated else None
    return _workbench(request).submit(event, access.principal, profile=profile)


@router.get("/profiles")
def profiles(request: Request, access: Reader):
    engine = _workbench(request)
    engine.check_access(access.principal)
    return {"workspace": engine.workspace, "active": _profile(request).describe(),
            "available": [profile.describe() for profile in list_profiles()]}


@router.post("/reviews", status_code=201)
def prepare_review(request: Request, proposal: ReviewInput, access: Writer):
    return _profile(request).prepare(_workbench(request), access.principal, **proposal.model_dump())


@router.get("/cases/{case_id}")
def case_detail(request: Request, case_id: CaseID, access: Reader):
    case = _workbench(request).get_case(case_id, access.principal)
    if _signed_service_only(request, access):
        _hide_direct_decision(case)
    return case


@router.post("/approval-relay")
def relay_approval(request: Request, approval: SignedApprovalIn, access: Writer):
    relay = getattr(request.app.state, "workbench_approval_relay", None)
    if relay is None:
        raise HTTPException(404, detail="approval_relay_disabled")
    _workbench(request).check_access(access.principal)
    return relay.decide(approval.envelope, approval.signature)


@router.get("/cases/{case_id}/history")
def history(request: Request, case_id: CaseID, access: Reader):
    return _workbench(request).history(case_id, access.principal)


@router.post("/cases/{case_id}/decisions")
def decide(request: Request, case_id: CaseID, decision: DecisionIn, access: Writer):
    if _signed_service_only(request, access):
        raise HTTPException(403, detail="signed_service_approval_required")
    workbench = _workbench(request)
    if not _tracks_activity(workbench, access):
        return workbench.decide(case_id, access.principal, **decision.model_dump())

    def recorded():
        _record_activity(request, access, case_id, None, dt.datetime.now(dt.timezone.utc))

    return workbench.decide(case_id, access.principal, **decision.model_dump(), _on_recorded=recorded)


@router.post("/cases/{case_id}/feedback")
def feedback(request: Request, case_id: CaseID, feedback: FeedbackIn, access: Writer):
    _workbench(request).feedback(case_id, access.principal, **feedback.model_dump())
    return {"recorded": True}


@router.post("/chat")
def chat(request: Request, chat: ChatIn, access: Writer):
    workbench = _workbench(request)
    if not _tracks_activity(workbench, access):
        return workbench.chat(access.principal, **chat.model_dump())

    def advised(case_id, started_at, finished_at, completed, tokens):
        _record_activity(request, access, case_id, started_at, finished_at,
                         agent=chat.agent, completed=completed, tokens=tokens)

    return workbench.chat(access.principal, **chat.model_dump(), _on_advice=advised)
