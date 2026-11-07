"""Private, BU-scoped work activity metadata and measured token evidence."""

from __future__ import annotations

import datetime as dt
import hmac
import json
import logging
import os
import secrets
import sqlite3
import unicodedata
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator, model_validator

from . import seclog
from .routers.shadow_ai import _check_origin
from .security import READ, SPACES_WRITE, Access, SecurityDenied, require

_LIMIT = 300
_MAX_PERIOD = dt.timedelta(days=90)
_CLOCK_SKEW = dt.timedelta(minutes=5)
_DB_NAME = "activity.sqlite3"
_GLOBAL_WORKBENCH = "*"
_DISPLAY_PUNCTUATION = " ._()[]-'’–—,+"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS activity_events (
    owner TEXT NOT NULL,
    unit TEXT NOT NULL,
    id TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (owner, unit, id)
) WITHOUT ROWID
"""
_INDEX = """
CREATE INDEX IF NOT EXISTS activity_owner_time
ON activity_events (owner, finished_at DESC, id DESC, unit)
"""
_GLOBAL_INDEX = """
CREATE INDEX IF NOT EXISTS activity_time
ON activity_events (finished_at DESC, id DESC, unit, owner)
"""

EventID = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$", min_length=32, max_length=32)]
AgentID = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$", max_length=64)]
UnitID = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$", max_length=128)]
Reference = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", max_length=64)]
Timestamp = Annotated[str, StringConstraints(
    pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])$",
    max_length=32,
)]
Count = Annotated[int, Field(ge=0, le=10**9)]
Reader = Annotated[Access, Depends(require(READ))]
# Reuse the editor/admin grant and derive its BU scope from the same grant.
Writer = Annotated[Access, Depends(require(SPACES_WRITE, write=True))]
Source = Literal["reported", "workbench"]


def _display(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    if (not value or value != value.strip() or not value[0].isalnum() or ".." in value
            or any(not (char.isalnum() or char in _DISPLAY_PUNCTUATION
                        or unicodedata.category(char) in {"Mn", "Mc"}) for char in value)):
        raise ValueError("invalid display label")
    return value


def _utc(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value).astimezone(dt.UTC)


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Tokens(_Input):
    input: Count | None = None
    output: Count | None = None
    total: Count | None = None

    @model_validator(mode="after")
    def validate_totals(self) -> Tokens:
        if self.input is not None and self.output is not None:
            combined = self.input + self.output
            if combined > 10**9 or (self.total is not None and self.total != combined):
                raise ValueError("inconsistent token totals")
        elif self.total is not None and any(
            item is not None and item > self.total for item in (self.input, self.output)
        ):
            raise ValueError("inconsistent token totals")
        return self

    def projection(self, *, derive_total: bool) -> dict[str, int | None] | None:
        total = self.total
        if derive_total and total is None and self.input is not None and self.output is not None:
            total = self.input + self.output
        if self.input is None and self.output is None and total is None:
            return None
        return {"input": self.input, "output": self.output, "total": total}


class Support(_Input):
    kind: Literal["file", "support", "work"]
    label: str = Field(min_length=1, max_length=120)
    ref: Reference | None = None

    _validate_label = field_validator("label")(_display)

    @field_validator("ref")
    @classmethod
    def opaque_ref(cls, value: str | None) -> str | None:
        if value is not None and ".." in value:
            raise ValueError("invalid reference")
        return value


class ActivityIn(_Input):
    id: EventID
    actor_type: Literal["agent", "user"]
    actor_id: AgentID | None = None
    unit: UnitID | None = None
    action: str = Field(min_length=1, max_length=100)
    started_at: Timestamp | None = None
    finished_at: Timestamp
    status: Literal["completed", "failed", "waiting"]
    tokens: Tokens | None = None
    supports: list[Support] = Field(default_factory=list, max_length=8)

    _validate_action = field_validator("action")(_display)

    @model_validator(mode="after")
    def validate_evidence(self) -> ActivityIn:
        if (self.actor_type == "agent") != (self.actor_id is not None):
            raise ValueError("user attribution is server-owned and agent key is required")
        finish = _parse(self.finished_at)
        if self.started_at is not None and not dt.timedelta() <= finish - _parse(self.started_at) <= _MAX_PERIOD:
            raise ValueError("invalid activity interval")
        return self

    def projection(self, unit: str, source: Source, now: dt.datetime) -> dict[str, Any]:
        finish = _parse(self.finished_at)
        start = _parse(self.started_at) if self.started_at is not None else None
        if finish > now + _CLOCK_SKEW:
            raise HTTPException(422, detail="invalid_activity")
        return {
            "id": self.id, "actor_type": self.actor_type, "actor_id": self.actor_id,
            "unit": unit if unit not in {"", _GLOBAL_WORKBENCH} else None,
            "action": self.action, "status": self.status, "source": source,
            "started_at": _utc(start) if start is not None else None, "finished_at": _utc(finish),
            "duration_ms": (finish - start) // dt.timedelta(milliseconds=1) if start is not None else None,
            "tokens": self.tokens.projection(derive_total=source == "reported") if self.tokens is not None else None,
            "supports": [item.model_dump() for item in self.supports],
        }


@dataclass(frozen=True)
class ActivityScope:
    subject: str
    units: tuple[str, ...]
    can_view_all: bool
    global_scope: bool = False

    @property
    def readable_units(self) -> tuple[str, ...]:
        return (*self.units, _GLOBAL_WORKBENCH) if self.global_scope else self.units


class ActivityJournal:
    def __init__(self, state_dir: str, clock: Callable[[], dt.datetime] | None = None) -> None:
        self.path = Path(state_dir) / _DB_NAME
        self.clock = clock or (lambda: dt.datetime.now(dt.UTC))
        # A view token is an opaque browser invalidation marker, never an access grant.
        self.view_key = secrets.token_bytes(32)

    def view(self, scope: ActivityScope) -> str:
        context = [scope.subject, scope.readable_units, scope.can_view_all]
        return hmac.digest(self.view_key, json.dumps(context).encode(), "sha256").hex()

    def append(self, owner: str, unit: str, event: dict[str, Any]) -> bool:
        """Return whether this exact event was already recorded."""
        payload = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        key = (owner, unit, event["id"])
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except FileExistsError:
            pass
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=3)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(_SCHEMA)
            db.execute(_INDEX)
            db.execute(_GLOBAL_INDEX)
            inserted = db.execute("""
                INSERT INTO activity_events (owner, unit, id, finished_at, payload) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (owner, unit, id) DO NOTHING
            """, (*key, event["finished_at"], payload))
            if inserted.rowcount == 1:
                return False
            prior = db.execute("""
                SELECT payload FROM activity_events WHERE owner = ? AND unit = ? AND id = ?
            """, key).fetchone()
            if prior != (payload,):
                raise HTTPException(409, detail="activity_id_conflict")
            return True

    def list(self, owner: str | None, units: tuple[str, ...], since: str, until: str) -> tuple[list[dict[str, Any]], bool]:
        if not units or not self.path.is_file():
            return [], False
        owner_filter = "owner = ? AND " if owner is not None else ""
        marks = ", ".join("?" for _ in units)
        query = f"""
            SELECT owner, payload FROM activity_events WHERE {owner_filter}finished_at >= ? AND finished_at <= ?
            AND unit IN ({marks})
            ORDER BY finished_at DESC, id DESC, unit ASC, owner ASC LIMIT ?
        """
        params = (() if owner is None else (owner,)) + (since, until, *units, _LIMIT + 1)
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)) as db:
            # A reader can reach a newly opened database before its initial write commits.
            if not db.execute("SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'activity_events'").fetchone():
                return [], False
            rows = db.execute(query, params).fetchall()
        events = []
        for subject, payload in rows[:_LIMIT]:
            event = json.loads(payload)
            event["user_id"] = subject
            if event["actor_type"] == "user":
                event["actor_id"] = subject
            events.append(event)
        return events, len(rows) > _LIMIT


def _scope(request: Request, access: Access) -> ActivityScope:
    principal = access.principal
    if principal.auth_mode != "proxy" or not principal.authenticated or not principal.user:
        raise HTTPException(403, detail="activity_identity_required")
    svc = request.app.state.service
    scope = svc.business_scope(access)
    if scope is None:
        admin_group = any(role == "admin" and group in principal.groups
                          for group, role in svc.guard.config.role_map)
        return ActivityScope(principal.user, ("",), admin_group)
    known = {row["id"] for row in svc.config.get()["factory"]["cost_centers"]}
    units = tuple(sorted(known if scope.all_units else scope.centers & known))
    return ActivityScope(principal.user, units, scope.global_admin, scope.all_units)


def _record(request: Request, access: Access, body: ActivityIn, source: Source) -> bool:
    scope = _scope(request, access)
    unit = body.unit
    # Workbench's global workspace has no case BU. Never guess a unit or let
    # reported records use its internal partition.
    if unit is None and source == "workbench" and scope.global_scope:
        unit = _GLOBAL_WORKBENCH
    if unit is None:
        if len(scope.units) != 1:
            raise HTTPException(422, detail="activity_unit_required")
        unit = scope.units[0]
    if unit not in (scope.readable_units if source == "workbench" else scope.units):
        raise HTTPException(403, detail="activity_scope_forbidden")
    log: ActivityJournal = request.app.state.activity
    event = body.projection(unit, source, log.clock())
    return log.append(scope.subject, unit, event)


def record_workbench_activity(request: Request, access: Access, event: dict[str, Any]) -> bool:
    """Record operational metadata without changing a Workbench outcome."""
    if getattr(request.app.state, "activity", None) is None:
        return False
    try:
        try:
            tokens = Tokens.model_validate(event["tokens"]) if event.get("tokens") is not None else None
        except ValidationError:
            tokens = None
        _record(request, access, ActivityIn.model_validate({**event, "tokens": tokens}), "workbench")
    except (HTTPException, SecurityDenied, OSError, sqlite3.Error, ValueError, OverflowError):
        return False
    return True


class ActivityRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request):
            _check_origin(request)
            if request.method == "POST":
                types = request.headers.getlist("content-type")
                if len(types) != 1 or types[0].split(";", 1)[0].strip().lower() != "application/json":
                    raise HTTPException(415, detail="json_required")
            try:
                return await handler(request)
            except (RequestValidationError, OverflowError):
                raise HTTPException(422, detail="invalid_activity") from None
            except (OSError, sqlite3.Error, json.JSONDecodeError):
                raise HTTPException(503, detail="activity_unavailable") from None

        return handle


router = APIRouter(prefix="/api/v1/activity", tags=["activity"], route_class=ActivityRoute)


def _audit_all(scope: ActivityScope, access: Access, *, status: int, count: int | None = None) -> None:
    seclog.emit(seclog.SECURITY_LOGGER, "activity.global_view",
                logging.INFO if status == 200 else logging.WARNING, status=status, records=count,
                actor_ref=seclog.reference("actor", scope.subject),
                scope_ref=seclog.reference("activity-scope", ",".join(scope.readable_units)),
                request_ref=seclog.reference("request", access.request_id))


@router.get("")
def history(request: Request, access: Reader, days: Literal["7", "30", "90"] = "7",
            view: Literal["own", "all"] = "own"):
    pairs = request.query_params.multi_items()
    keys = {key for key, _ in pairs}
    if not keys <= {"days", "lang", "view"} or len(keys) != len(pairs):
        raise HTTPException(422, detail="invalid_activity")
    scope = _scope(request, access)
    if view == "all" and not scope.can_view_all:
        _audit_all(scope, access, status=403)
        raise HTTPException(403, detail="activity_scope_forbidden")
    log: ActivityJournal = request.app.state.activity
    now = log.clock().astimezone(dt.UTC)
    since = _utc(now.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=int(days) - 1))
    until = _utc(now)
    events, truncated = log.list(scope.subject if view == "own" else None, scope.readable_units, since, until)
    if view == "all":
        _audit_all(scope, access, status=200, count=len(events))
    return {
        "events": events, "truncated": truncated, "since": since, "until": until,
        "view": log.view(scope), "can_view_all": scope.can_view_all,
    }


@router.post("")
def record(request: Request, body: ActivityIn, access: Writer):
    duplicate = _record(request, access, body, "reported")
    return {"recorded": True, "duplicate": duplicate}
