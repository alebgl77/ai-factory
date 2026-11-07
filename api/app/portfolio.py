"""Private, BU-scoped goals, projects and work coordination."""

from __future__ import annotations

import datetime as dt
import hmac
import json
import sqlite3
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from .bu_access import PORTFOLIO_AGENT_MODES
from .i18n import localize_tree
from .pagination import Cursor, decode_cursor, encode_cursor
from .portfolio_store import TASK_PAGE_LIMIT, PortfolioActor, PortfolioError, PortfolioStore
from .routers.shadow_ai import _check_origin
from .security import READ, ROLE_PERMISSIONS, Access, require

EntityID = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$", min_length=32, max_length=32)]
Key = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$", max_length=128)]
Reference = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", max_length=64)]
Date = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", max_length=10)]
Title = Annotated[str, StringConstraints(min_length=1, max_length=120)]
Description = Annotated[str, StringConstraints(max_length=1200)]
Revision = Annotated[int, Field(ge=1, le=10**9)]
TokenCount = Annotated[int, Field(ge=0, le=10**9)]
Amount = Annotated[float, Field(ge=0, le=10**12, allow_inf_nan=False)]
Priority = Literal["low", "normal", "high", "critical"]
ProjectState = Literal["planned", "active", "done", "archived"]
Direction = Literal["at_least", "at_most"]
Reader = Annotated[Access, Depends(require(READ))]
Writer = Annotated[Access, Depends(require(READ, write=True))]
Assignment = tuple[str, Literal["employee", "agent"], str]


def _text(value: str) -> str:
    if value != value.strip() or any(unicodedata.category(c).startswith("C") and c != "\n" for c in value):
        raise ValueError("invalid text")
    return value


def _date(value: str | None) -> str | None:
    if value is not None:
        dt.date.fromisoformat(value)
    return value


class Input(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class Plan(Input):
    title: Title
    description: Description = ""
    due_on: Date | None = None

    _text_fields = field_validator("title", "description")(_text)
    _calendar_date = field_validator("due_on")(_date)


class GoalIn(Plan):
    id: EntityID
    metric: Title
    target: Amount
    current: Amount | None = None
    direction: Direction = "at_least"

    _metric_text = field_validator("metric")(_text)


class ProjectIn(Plan):
    id: EntityID
    goal_id: EntityID
    owner_unit: Key
    unit_ids: list[Key] = Field(min_length=1, max_length=32)
    state: ProjectState = "planned"
    usecase_id: Key | None = None

    @model_validator(mode="after")
    def participating_units(self) -> ProjectIn:
        if self.owner_unit not in self.unit_ids or len(set(self.unit_ids)) != len(self.unit_ids):
            raise ValueError("invalid project units")
        return self


class TaskIn(Plan):
    project_id: EntityID
    unit: Key
    priority: Priority = "normal"
    execution: Literal["human", "read_only", "prepare", "sensitive"] = "human"
    technical: bool = False
    dependency_ids: list[EntityID] = Field(default_factory=list, max_length=12)


class PlanPatch(Input):
    revision: Revision
    title: Title | None = None
    description: Description | None = None
    due_on: Date | None = None

    @field_validator("title", "description", "metric", check_fields=False)
    @classmethod
    def text_fields(cls, value: str | None) -> str | None:
        return _text(value) if value is not None else None

    _calendar_date = field_validator("due_on")(_date)

    @model_validator(mode="after")
    def changed_fields(self) -> PlanPatch:
        changes = self.model_fields_set - {"revision"}
        if not changes or any(getattr(self, key) is None for key in changes - {"due_on", "current"}):
            raise ValueError("invalid patch")
        return self


class GoalPatch(PlanPatch):
    metric: Title | None = None
    target: Amount | None = None
    current: Amount | None = None
    direction: Direction | None = None


class ProjectPatch(PlanPatch):
    state: ProjectState | None = None


class TaskPatch(PlanPatch):
    priority: Priority | None = None
    dependency_ids: list[EntityID] | None = Field(default=None, max_length=12)


class Assignee(Input):
    kind: Literal["employee", "agent"]
    id: Key


class Evidence(Input):
    kind: Literal["file", "support", "work"]
    label: Title
    ref: Reference

    @field_validator("label", "ref")
    @classmethod
    def no_path_or_controls(cls, value: str) -> str:
        if any(c in value for c in ("/", "\\", ":", "\n", "..")):
            raise ValueError("invalid evidence reference")
        return _text(value)


class ReportedTokens(Input):
    input: TokenCount
    output: TokenCount


class CommandIn(Input):
    revision: Revision
    action: Literal["assign", "authorize_start", "start", "block", "unblock", "report_usage", "submit",
                    "approve", "request_changes", "cancel"]
    assignee: Assignee | None = None
    evidence: list[Evidence] | None = Field(default=None, max_length=8)
    tokens: ReportedTokens | None = None

    @model_validator(mode="after")
    def command_shape(self) -> CommandIn:
        if (self.action == "assign") != (self.assignee is not None):
            raise ValueError("invalid assignment")
        if "evidence" in self.model_fields_set and (self.action != "submit" or self.evidence is None):
            raise ValueError("unexpected evidence")
        if "tokens" in self.model_fields_set and (self.action not in {"report_usage", "submit"} or self.tokens is None):
            raise ValueError("unexpected tokens")
        if self.action == "report_usage" and self.tokens is None:
            raise ValueError("missing tokens")
        return self


class DelegationIn(Input):
    revision: Revision
    mode: Literal["manual", "automatic"]
    title: Title
    description: Description = ""
    agent_id: Key | None = None

    _text_fields = field_validator("title", "description")(_text)

    @model_validator(mode="after")
    def selection(self) -> DelegationIn:
        if (self.mode == "manual") != (self.agent_id is not None):
            raise ValueError("invalid delegation selection")
        return self


@dataclass(frozen=True)
class PortfolioContext:
    global_admin: bool
    roles: dict[str, frozenset[str]]
    own: tuple[Assignment, ...]
    units: dict[str, str]
    people: dict[tuple[str, str], str]
    agents: dict[str, dict[str, Any]]
    agent_cases: dict[str, str]
    case_units: dict[str, str]
    autonomy_by_unit: dict[str, dict[str, str]]
    agent_subject: bool
    grant_generation: str
    actor: PortfolioActor
    view: str
    participating_units: frozenset[str]
    cursor_scope: str
    runner_agents: frozenset[str] = frozenset()
    runner_available: frozenset[str] = frozenset()

    @property
    def managed_units(self) -> frozenset[str]:
        return frozenset(self.units if self.global_admin else self.roles)

    def sees(self, task: dict[str, Any]) -> bool:
        return task["unit"] in self.managed_units or self.is_assignee(task)

    def is_assignee(self, task: dict[str, Any]) -> bool:
        assignee = task.get("assignee")
        return assignee is not None and (task["unit"], assignee["kind"], assignee["id"]) in self.own

    def require_role(self, unit: str, role: str) -> None:
        if role not in self.roles.get(unit, ()):
            raise PortfolioError("portfolio_forbidden", status=403)

    def require_global(self) -> None:
        if not self.global_admin:
            raise PortfolioError("portfolio_forbidden", status=403)

    def autonomy(self, task: dict[str, Any]) -> str | None:
        return self.autonomy_by_unit.get(task["unit"], {}).get(task["execution"])

    def project_task(self, task: dict[str, Any]) -> dict[str, Any]:
        autonomy = self.autonomy(task)
        tokens = task.get("tokens")
        assignee = task["assignee"] or {}
        return {**task, "autonomy": autonomy, "tokens": None if tokens is None else
                {**tokens, "source": tokens.get("source", "agent_reported")},
                "runner_managed": bool(task["parent_task_id"] and assignee.get("kind") == "agent"
                                       and assignee.get("id") in self.runner_agents),
                "runner_available": bool(task["parent_task_id"] and assignee.get("kind") == "agent"
                                         and assignee.get("id") in self.runner_available),
                "start_authorized": autonomy == "human_review" and task["start_authorized"]}

    def ready_agent(self, unit: str, agent_id: str) -> bool:
        agent = self.agents.get(agent_id)
        return agent is not None and agent["unit"] == unit and agent["ready"]

    def require_agent(self, task: dict[str, Any], agent_id: str) -> None:
        if self.autonomy(task) == "blocked":
            raise PortfolioError("portfolio_agent_blocked", status=403)
        if not self.ready_agent(task["unit"], agent_id):
            raise PortfolioError("portfolio_assignee_unavailable", status=422)

    def can_block_unavailable_agent(self, task: dict[str, Any]) -> bool:
        unit = task["unit"]
        assignee = task["assignee"] or {}
        return ("manager" in self.roles.get(unit, ()) and assignee.get("kind") == "agent"
                and (self.autonomy(task) == "blocked" or not self.ready_agent(unit, assignee["id"])))


def _context(request: Request, access: Access) -> PortfolioContext:
    svc = request.app.state.service
    principal = access.principal
    policy = svc.bu_policy
    subject = principal.user
    if policy is None or principal.auth_mode != "proxy" or not principal.authenticated or not subject:
        raise HTTPException(403, detail="portfolio_forbidden")
    scope = svc.business_scope(access)
    assert scope is not None and svc.booking_key is not None
    cfg = svc.config.get()
    lang = svc.lang(request.query_params.get("lang"))
    units = {row["id"]: localize_tree(row.get("name", row["id"]), lang)
             for row in cfg["factory"].get("cost_centers", ())}
    profiles = {row["id"]: row["name"] for row in cfg["profiles"].get("people", ())}
    people = {(unit, person): localize_tree(profiles[person], lang)
              for unit, members in policy.people.items() if unit in units
              for person in members if person in profiles}
    case_units = {row["id"]: row["cost_center"] for row in cfg.get("usecases", ())
                  if row.get("cost_center") in units}
    bound_agents = frozenset(policy.portfolio_agents.values())
    agents = {
        row["id"]: {"id": row["id"], "label": localize_tree(row.get("name", row["id"]), lang),
                    "unit": case_units[row["usecase"]],
                    "ready": row.get("eval_status") == "passed" and row["id"] in bound_agents}
        for row in cfg["catalog"].get("agents", ()) if row.get("usecase") in case_units
    }
    agent_cases = {row["id"]: row["usecase"] for row in cfg["catalog"].get("agents", ()) if row["id"] in agents}

    # A service identity remains an agent, even if the IdP mistakenly adds a
    # staff group to it. Human capabilities cannot ride along on that binding.
    agent = policy.portfolio_agents.get(subject)
    employee = policy.identities.get(subject)
    roles: dict[str, frozenset[str]] = {}
    own: tuple[Assignment, ...] = ()
    if agent is None:
        for group, role in svc.guard.config.role_map:
            domain = policy.portfolio_roles.get(group, frozenset())
            if group not in principal.groups or READ not in ROLE_PERMISSIONS[role] or not domain:
                continue
            allowed = policy.groups[group]
            for unit in units:
                if unit in allowed or "*" in allowed:
                    roles[unit] = roles.get(unit, frozenset()) | domain
        if employee is not None:
            own = tuple(sorted((unit, "employee", employee) for unit in units
                               if scope.allows(unit) and (unit, employee) in people))
    elif agent in agents and agents[agent]["ready"] and scope.allows(agents[agent]["unit"]):
        own = ((agents[agent]["unit"], "agent", agent),)

    global_admin = agent is None and scope.global_admin
    autonomy = {
        unit: {mode: policy.portfolio_autonomy.get(unit, {}).get(mode, "human_review")
               for mode in PORTFOLIO_AGENT_MODES}
        for unit in units
    }
    actor_ref = hmac.digest(svc.booking_key, b"factory-portfolio-actor-v1\0" + subject.encode(), "sha256").hex()
    actor_label = (localize_tree(profiles[employee], lang) if employee in profiles else
                   agents[agent]["label"] if agent in agents else
                   "Direction" if global_admin else "Responsable habilité")
    grant_generation = request.app.state.portfolio_grant_generation
    participating = ((scope.centers | roles.keys() | {unit for unit, _, _ in own}) & units.keys()
                     if agent is None else {unit for unit, _, _ in own})
    grants = [actor_ref, global_admin, sorted(own),
              sorted(group for group in principal.groups if group in policy.groups),
              sorted((unit, sorted(items)) for unit, items in roles.items()), sorted(participating)]
    view_key = json.dumps([grant_generation, grants], separators=(",", ":"))
    view = hmac.digest(svc.booking_key, b"factory-portfolio-view-v1\0" + view_key.encode(), "sha256").hex()
    cursor_scope = json.dumps([policy.fingerprint, grants, sorted(units)], separators=(",", ":"))
    engine = getattr(request.app.state, "portfolio_engine", None)
    runner_agents = engine.managed_agents() if engine is not None else frozenset()
    runner_available = engine.available_agents(cfg) if engine is not None else frozenset()
    return PortfolioContext(global_admin, roles, own, units, people, agents, agent_cases, case_units,
                            autonomy, agent is not None, grant_generation, PortfolioActor(actor_ref, actor_label), view,
                            frozenset(participating), cursor_scope,
                            runner_agents=runner_agents, runner_available=runner_available)


class PortfolioRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request):
            _check_origin(request)
            pairs = request.query_params.multi_items()
            allowed = {"lang", "limit", "cursor"} if self.endpoint is snapshot else {"lang"}
            if len(pairs) != len(request.query_params) or any(key not in allowed for key, _ in pairs):
                raise HTTPException(422, detail="invalid_portfolio_request")
            if request.method in {"POST", "PUT"}:
                types = request.headers.getlist("content-type")
                if len(types) != 1 or types[0].split(";", 1)[0].strip().lower() != "application/json":
                    raise HTTPException(415, detail="json_required")
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, detail="invalid_portfolio_request") from None
            except PortfolioError as exc:
                raise HTTPException(exc.status, detail=exc.code) from None
            except (OSError, sqlite3.Error, json.JSONDecodeError):
                raise HTTPException(503, detail="portfolio_unavailable") from None

        return handle


router = APIRouter(prefix="/api/v1/portfolio", tags=["portfolio"], route_class=PortfolioRoute)


def _store(request: Request) -> PortfolioStore:
    return request.app.state.portfolio


@router.get("")
def snapshot(request: Request, access: Reader, limit: Annotated[int, Query(ge=1, le=TASK_PAGE_LIMIT)] | None = None,
             cursor: Cursor | None = None):
    ctx = _context(request, access)
    key = request.app.state.service.booking_key
    page_scope = ["portfolio", ctx.cursor_scope, limit]
    before = None
    if cursor is not None:
        position = decode_cursor(key, page_scope, cursor)
        if limit is None or not position or len(position) != 1:
            raise HTTPException(422, detail="invalid_portfolio_cursor")
        before = position[0]
    data = _store(request).snapshot(managed_units=ctx.managed_units, own_assignments=ctx.own,
                                    policy_fingerprint=ctx.grant_generation, limit=limit, before=before,
                                    agent_view=ctx.agent_subject)
    if limit is not None:
        position = data.pop("next_position")
        data["next_cursor"] = encode_cursor(key, page_scope, [position]) if position else None
    scope = ctx.managed_units
    visible_units = scope | {unit for unit, _, _ in ctx.own}
    personal = {(unit, identifier) for unit, kind, identifier in ctx.own if kind == "employee"}
    own_agents = {identifier for _, kind, identifier in ctx.own if kind == "agent"}
    if not ctx.global_admin and not ctx.agent_subject:
        detailed = {project["id"] for project in data["projects"]
                    if ctx.participating_units.intersection(project["unit_ids"])}
        detailed_goals = {project["goal_id"] for project in data["projects"] if project["id"] in detailed}
        data["projects"] = [project if project["id"] in detailed else
                            {"title": project["title"], "state": project["state"]} for project in data["projects"]]
        data["goals"] = [goal if goal["id"] in detailed_goals else
                         {"title": goal["title"], "status": _goal_status(goal)} for goal in data["goals"]]
    return {
        **data,
        "tasks": [ctx.project_task(task) for task in data["tasks"]],
        "people": [{"id": person, "label": label, "unit": unit}
                   for (unit, person), label in sorted(ctx.people.items())
                   if unit in scope or (unit, person) in personal],
        "agents": [{**agent, "runner_managed": agent["id"] in ctx.runner_agents,
                    "runner_available": agent["id"] in ctx.runner_available}
                   for _, agent in sorted(ctx.agents.items())
                   if agent["unit"] in scope or agent["id"] in own_agents],
        "units": [{"id": unit, "label": label} for unit, label in sorted(ctx.units.items())
                  if ctx.global_admin or unit in ctx.participating_units],
        "capabilities": {
            "global_admin": ctx.global_admin,
            "roles_by_unit": {unit: sorted(roles) for unit, roles in ctx.roles.items()},
            "employee_id": next((identifier for _, kind, identifier in ctx.own if kind == "employee"), None),
            "agent_id": next(iter(own_agents), None),
            "autonomy_by_unit": {unit: ctx.autonomy_by_unit[unit] for unit in sorted(visible_units)},
        },
        "view": ctx.view,
    }


def _goal_status(goal: dict[str, Any]) -> str:
    current = goal["current"]
    if current is None:
        return "unmeasured"
    met = current <= goal["target"] if goal["direction"] == "at_most" else current >= goal["target"]
    return "met" if met else "unmet"


@router.post("/goals", status_code=201)
def create_goal(request: Request, data: GoalIn, access: Writer):
    ctx = _context(request, access)
    ctx.require_global()
    return _store(request).create_goal(data.id, data.model_dump(exclude={"id"}), ctx.actor)


@router.put("/goals/{goal_id}")
def update_goal(request: Request, goal_id: EntityID, data: GoalPatch, access: Writer):
    ctx = _context(request, access)
    ctx.require_global()
    return _store(request).update_goal(
        goal_id, data.revision, data.model_dump(exclude={"revision"}, exclude_unset=True), ctx.actor)


@router.post("/projects", status_code=201)
def create_project(request: Request, data: ProjectIn, access: Writer):
    ctx = _context(request, access)
    ctx.require_global()
    if not set(data.unit_ids) <= ctx.units.keys() or (
        data.usecase_id is not None and ctx.case_units.get(data.usecase_id) != data.owner_unit
    ):
        raise HTTPException(422, detail="invalid_portfolio_project_scope")
    return _store(request).create_project(data.id, data.model_dump(exclude={"id"}), ctx.actor)


@router.put("/projects/{project_id}")
def update_project(request: Request, project_id: EntityID, data: ProjectPatch, access: Writer):
    ctx = _context(request, access)
    ctx.require_global()
    return _store(request).update_project(
        project_id, data.revision, data.model_dump(exclude={"revision"}, exclude_unset=True), ctx.actor)


@router.post("/tasks", status_code=201)
def create_task(request: Request, data: TaskIn, access: Writer):
    ctx = _context(request, access)
    ctx.require_role(data.unit, "project_lead")
    return ctx.project_task(_store(request).create_task(uuid4().hex, data.model_dump(), ctx.actor))


def _ensure_visible(ctx: PortfolioContext, task: dict[str, Any]) -> None:
    if not ctx.sees(task):
        raise PortfolioError("task_not_found", status=404)


@router.put("/tasks/{task_id}")
def update_task(request: Request, task_id: EntityID, data: TaskPatch, access: Writer):
    ctx = _context(request, access)

    def authorize(task: dict[str, Any]) -> None:
        _ensure_visible(ctx, task)
        ctx.require_role(task["unit"], "project_lead")

    return ctx.project_task(_store(request).update_task(
        task_id, data.revision, data.model_dump(exclude={"revision"}, exclude_unset=True), ctx.actor, authorize))


@router.post("/tasks/{task_id}/commands")
def command_task(request: Request, task_id: EntityID, data: CommandIn, access: Writer):
    ctx = _context(request, access)

    def authorize(task: dict[str, Any]) -> None:
        _ensure_visible(ctx, task)
        unit = task["unit"]
        if data.action == "assign":
            ctx.require_role(unit, "manager")
            assert data.assignee is not None
            if data.assignee.kind == "employee":
                if (unit, data.assignee.id) not in ctx.people:
                    raise PortfolioError("portfolio_assignee_unavailable", status=422)
            else:
                ctx.require_agent(task, data.assignee.id)
        elif data.action == "authorize_start":
            ctx.require_role(unit, "manager")
            assignee = task["assignee"] or {}
            if assignee.get("kind") != "agent" or ctx.autonomy(task) != "human_review":
                raise PortfolioError("portfolio_start_not_reviewable", status=403)
            ctx.require_agent(task, assignee["id"])
        elif data.action in {"approve", "request_changes"}:
            ctx.require_role(unit, "architect")
            if ctx.is_assignee(task):
                raise PortfolioError("separation_of_duties", status=403)
        elif data.action == "cancel":
            ctx.require_role(unit, "manager")
        elif not ctx.is_assignee(task):
            if data.action != "block" or not ctx.can_block_unavailable_agent(task):
                raise PortfolioError("portfolio_forbidden", status=403)
        elif task["assignee"]["kind"] == "agent" and data.action != "block":
            ctx.require_agent(task, task["assignee"]["id"])
            if (data.action in {"start", "submit", "report_usage"} and task["parent_task_id"]
                    and task["assignee"]["id"] in ctx.runner_agents):
                raise PortfolioError("portfolio_runner_managed", status=403)
            if data.action == "start" and ctx.autonomy(task) == "human_review" and not task["start_authorized"]:
                raise PortfolioError("portfolio_start_requires_review", status=403)
        elif data.tokens is not None:
            raise PortfolioError("portfolio_agent_tokens_only", status=403)

    command = data.model_dump(exclude={"revision"}, exclude_none=True)
    if data.tokens is not None:
        command["tokens"]["total"] = data.tokens.input + data.tokens.output
    workbench = getattr(request.app.state, "workbench", None)
    aliases = tuple(sorted(access.principal.ids)) if getattr(workbench, "mode", None) == "portfolio" else None
    return ctx.project_task(_store(request).command_task(
        task_id, data.revision, command, ctx.actor, authorize, policy_fingerprint=ctx.grant_generation,
        workbench_aliases=aliases))


@router.post("/tasks/{parent_id}/subagents", status_code=201)
def delegate_task(request: Request, parent_id: EntityID, data: DelegationIn, access: Writer):
    ctx = _context(request, access)

    def select(parent: dict[str, Any], child_load: Mapping[str, int]) -> str:
        _ensure_visible(ctx, parent)
        unit = parent["unit"]
        manager = "manager" in ctx.roles.get(unit, ())
        if not manager and (data.mode != "automatic" or not ctx.agent_subject or not ctx.is_assignee(parent)):
            raise PortfolioError("portfolio_forbidden", status=403)

        assignee = parent["assignee"] or {}
        if assignee.get("kind") != "agent":
            raise PortfolioError("portfolio_parent_agent_required", status=422)
        parent_agent = assignee["id"]
        ctx.require_agent(parent, parent_agent)
        child_mode = "read_only" if parent["execution"] == "read_only" else "prepare"
        autonomy = ctx.autonomy_by_unit[unit][child_mode]
        if autonomy == "blocked":
            raise PortfolioError("portfolio_agent_blocked", status=403)
        if autonomy != "autonomous" and not manager:
            raise PortfolioError("portfolio_start_requires_review", status=403)

        if data.agent_id is not None:
            if data.agent_id == parent_agent or not ctx.ready_agent(unit, data.agent_id):
                raise PortfolioError("portfolio_assignee_unavailable", status=422)
            return data.agent_id
        candidates = [agent_id for agent_id in ctx.agents if agent_id != parent_agent and ctx.ready_agent(unit, agent_id)]
        if not candidates:
            raise PortfolioError("portfolio_assignee_unavailable", status=422)
        return min(candidates, key=lambda agent_id: (agent_id not in ctx.runner_available,
                                                      agent_id in ctx.runner_agents - ctx.runner_available,
                                                      ctx.agent_cases[agent_id] != ctx.agent_cases[parent_agent],
                                                      child_load.get(agent_id, 0), agent_id))

    result = _store(request).delegate_task(
        parent_id, data.revision, uuid4().hex,
        {"title": data.title, "description": data.description, "delegation": data.mode}, ctx.actor, select)
    return {"parent": ctx.project_task(result["parent"]), "task": ctx.project_task(result["task"])}
