"""Authenticated Factory idea board with local bilingual classification."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import StringConstraints, ValidationInfo, field_validator

from .pagination import Cursor, decode_cursor, encode_cursor
from .portfolio import EntityID, Input, Key, PortfolioContext, Reader, Title, Writer, _context
from .routers.shadow_ai import _check_origin
from .security import Access
from .state import StateError


_VISIBLE_LIMIT = 120
_BU_CAPACITY = 2_000
_ADMIN_SOURCE = "@factory-administration"
_FIELDS = "id, bu, title, text, category, tags, created_at"
_SCHEMA = """CREATE TABLE IF NOT EXISTS ideas (
    id TEXT PRIMARY KEY, bu TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL,
    category TEXT NOT NULL, tags TEXT NOT NULL, created_at TEXT NOT NULL
)"""
_ORIGIN_SCHEMA = """CREATE TABLE IF NOT EXISTS idea_origins (
    idea_id TEXT PRIMARY KEY REFERENCES ideas(id) ON DELETE CASCADE,
    subject TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('employee', 'agent', 'admin'))
)"""
AuthorKind = Literal["employee", "agent", "admin"]

# A match counts once per keyword and field. The order resolves equal scores.
_RULES = (
    ("security", (
        ("access", "acces access authentification authentication identite identity permission permissions sso mfa"),
        ("compliance", "audit conformite compliance cyber rgpd gdpr confidentialite privacy securite security"),
    )),
    ("automation", (
        ("workflow", "automatiser automatisation automate automation processus process workflow orchestration"),
        ("integration", "api connecteur connecteurs connector connectors crm erp integration integrations"),
    )),
    ("finance", (
        ("financial_analysis", "finance financier financiere financiers financieres financial revenue revenus "
         "marge marges margin margins rentabilite profitability comptable comptabilite accounting"),
        ("budget", "budget budgets budgetaire budgetaires tresorerie cash cout couts cost costs roi"),
    )),
    ("data", (
        ("analytics", "analyse analytics donnees data metrique metriques metrics reporting tableau dashboard"),
        ("quality", "qualite quality validation nettoyage cleaning doublon doublons duplicate duplicates"),
    )),
    ("experience", (
        ("usability", "accessibilite accessibility ergonomie interface navigation utilisateur users ux ui"),
        ("feedback", "avis feedback enquete survey satisfaction retour retours"),
    )),
    ("adoption", (
        ("training", "adoption formation former onboarding training tutoriel tutoriels tutorial atelier ateliers workshop"),
        ("documentation", "catalogue catalog documentation guide guides savoir knowledge"),
    )),
)
_TOKEN = re.compile(r"[a-z0-9]+")
_SEARCH_LABELS = {
    "security": "Security Sécurité", "automation": "Automation Automatisation", "finance": "Finance",
    "data": "Data & quality Données & qualité", "experience": "Experience Expérience",
    "adoption": "Learning & adoption Formation & adoption", "uncategorized": "To explore À explorer",
    "access": "Access Accès", "compliance": "Compliance Conformité", "workflow": "Workflow",
    "integration": "Integration Intégration", "financial_analysis": "Financial analysis Analyse financière",
    "budget": "Budget", "analytics": "Analyse Analytics", "quality": "Quality Qualité",
    "usability": "Usability Ergonomie", "feedback": "Feedback Retours", "training": "Training Formation",
    "documentation": "Documentation",
}
_CATEGORIES = frozenset(category for category, _ in _RULES) | {"uncategorized"}


def _fold(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))


def _words(value: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(_fold(value)))


def _search_text(title: str, text: str, category: str, tags: str, source_label: str, bu: str) -> str:
    terms = [category, *json.loads(tags)]
    return _fold(" ".join([title, text, *terms, *(_SEARCH_LABELS.get(term, "") for term in terms), source_label, bu]))


def classify_idea(title: str, text: str) -> tuple[str, list[str]]:
    title_words, text_words = _words(title), _words(text)
    best_score, category, tags = 0, "uncategorized", []
    for candidate, rules in _RULES:
        score, matches = 0, []
        for tag, keywords in rules:
            terms = frozenset(keywords.split())
            weight = 2 * len(terms & title_words) + len(terms & text_words)
            if weight:
                matches.append(tag)
                score += weight
        if score > best_score:
            best_score, category, tags = score, candidate, matches
    return category, tags


class IdeaIn(Input):
    bu: Key | Literal["@factory-administration"] | None = None
    title: Title
    text: Annotated[str, StringConstraints(min_length=1, max_length=1000)]

    @field_validator("bu")
    @classmethod
    def explicit_source(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("invalid source")
        return value

    @field_validator("title", "text")
    @classmethod
    def note_text(cls, value: str, info: ValidationInfo) -> str:
        if any(unicodedata.category(c).startswith("C") and (info.field_name == "title" or c != "\n") for c in value):
            raise ValueError("invalid note")
        value = value.strip()
        if not value:
            raise ValueError("invalid note")
        return value


class IdeasCapacity(RuntimeError):
    pass


class IdeasStore:
    def __init__(self, state_dir: str | Path, clock: Callable[[], dt.datetime] | None = None) -> None:
        self.path = Path(state_dir) / "ideas.sqlite3"
        self.clock = clock or (lambda: dt.datetime.now(dt.UTC))

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection | None]:
        if write:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            except FileExistsError:
                pass
        elif not self.path.is_file():
            yield None
            return

        mode = "rw" if write else "ro"
        uri = self.path.resolve().as_uri() + f"?mode={mode}"
        with closing(sqlite3.connect(uri, uri=True, timeout=3)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if write:
                db.execute(_SCHEMA)
                db.execute(_ORIGIN_SCHEMA)
                db.execute("CREATE INDEX IF NOT EXISTS ideas_bu ON ideas(bu, created_at DESC, id)")
            elif db.execute("SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'ideas'").fetchone() is None:
                # A first writer can create the file before it commits the schema.
                yield None
                return
            yield db

    @staticmethod
    def _card(row: sqlite3.Row) -> dict:
        return {**dict(row), "tags": json.loads(row["tags"]), "visibility": "factory"}

    def list(self, units: frozenset[str]) -> tuple[list[dict], int]:
        items, totals, _ = self.page(dict.fromkeys(units, ""), limit=_VISIBLE_LIMIT)
        return items, sum(totals.values())

    def page(self, sources: dict[str, str], *, limit: int, query: str = "", category: str | None = None,
             bu: str | None = None, position: list | None = None) -> tuple[list[dict], dict[str, int], list | None]:
        configured = json.dumps(sources)
        with self._transaction(write=False) as db:
            if db is None:
                return [], {}, None
            db.create_function("idea_search", 6, _search_text, deterministic=True)
            if position is None:
                newest = db.execute("""
                    SELECT id FROM ideas WHERE bu IN (SELECT key FROM json_each(?)) ORDER BY rowid DESC LIMIT 1
                """, (configured,)).fetchone()
                if newest is None:
                    return [], {}, None
                anchor, created_at, idea_id = newest["id"], None, None
            else:
                anchor, created_at, idea_id = position
            # The anchor excludes arrivals after the first page, including
            # imports or submissions with the same clock value.
            filters = """
                FROM ideas JOIN json_each(:sources) AS source ON ideas.bu = source.key
                WHERE ideas.rowid <= (SELECT rowid FROM ideas WHERE id = :anchor)
                  AND (:bu IS NULL OR ideas.bu = :bu)
                  AND (:query = '' OR instr(idea_search(title, text, category, tags, source.value, ideas.bu), :query))
            """
            args = {"sources": configured, "anchor": anchor, "bu": bu, "query": query, "category": category,
                    "created_at": created_at, "id": idea_id, "limit": limit + 1}
            totals = dict(db.execute(f"SELECT category, COUNT(*) {filters} GROUP BY category", args).fetchall())
            rows = db.execute(f"""
                SELECT ideas.id, bu, title, text, category, tags, created_at {filters}
                  AND (:category IS NULL OR category = :category)
                  AND (:created_at IS NULL OR created_at < :created_at OR (created_at = :created_at AND ideas.id > :id))
                ORDER BY created_at DESC, ideas.id LIMIT :limit
            """, args).fetchall()
            items = [self._card(row) for row in rows[:limit]]
            continuation = [anchor, items[-1]["created_at"], items[-1]["id"]] if len(rows) > limit else None
            return items, totals, continuation

    def create(self, idea: IdeaIn, *, bu: str, subject: str, kind: AuthorKind) -> dict:
        category, tags = classify_idea(idea.title, idea.text)
        created_at = self.clock().astimezone(dt.UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        card = {"id": uuid4().hex, "bu": bu, "title": idea.title, "text": idea.text,
                "category": category, "tags": tags,
                "created_at": created_at}
        with self._transaction(write=True) as db:
            assert db is not None
            if db.execute("SELECT 1 FROM ideas WHERE bu = ? LIMIT 1 OFFSET ?",
                          (bu, _BU_CAPACITY - 1)).fetchone():
                raise IdeasCapacity()
            db.execute(f"""INSERT INTO ideas ({_FIELDS})
                VALUES (:id, :bu, :title, :text, :category, :tags, :created_at)""",
                       {**card, "tags": json.dumps(tags)})
            db.execute("INSERT INTO idea_origins (idea_id, subject, kind) VALUES (?, ?, ?)",
                       (card["id"], subject, kind))
        return {**card, "visibility": "factory"}

    def origin(self, idea_id: str, units: frozenset[str]) -> dict | None:
        with self._transaction(write=False) as db:
            if db is None:
                return None
            if db.execute("SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'idea_origins'").fetchone() is None:
                return None
            row = db.execute("""
                SELECT ideas.id, ideas.bu, ideas.created_at, idea_origins.subject, idea_origins.kind
                FROM idea_origins JOIN ideas ON ideas.id = idea_origins.idea_id
                WHERE idea_origins.idea_id = ? AND ideas.bu IN (SELECT value FROM json_each(?))
            """, (idea_id, json.dumps(sorted(units)))).fetchone()
            return dict(row) if row is not None else None


class IdeasRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request):
            _check_origin(request)
            pairs = request.query_params.multi_items()
            allowed = {"lang", "q", "category", "bu", "limit", "cursor"} if request.method == "GET" else {"lang"}
            if len(pairs) != len(request.query_params) or any(key not in allowed for key, _ in pairs):
                raise HTTPException(422, detail="invalid_ideas_request")
            if request.method == "POST":
                types = request.headers.getlist("content-type")
                if len(types) != 1 or types[0].split(";", 1)[0].strip().lower() != "application/json":
                    raise HTTPException(415, detail="json_required")
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, detail="invalid_ideas_request") from None
            except IdeasCapacity:
                raise HTTPException(409, detail="ideas_capacity_reached") from None
            except (OSError, sqlite3.Error, json.JSONDecodeError):
                raise HTTPException(503, detail="ideas_unavailable") from None

        return handle


router = APIRouter(prefix="/api/v1/ideas", tags=["ideas"], route_class=IdeasRoute)


def _writable(ctx: PortfolioContext) -> frozenset[str]:
    own = frozenset(unit for unit, _, _ in ctx.own)
    return frozenset({_ADMIN_SOURCE}) if ctx.global_admin and not own else own


def _ideas_context(request: Request, access: Access) -> PortfolioContext:
    ctx = _context(request, access)
    if ctx.agent_subject and not ctx.own:
        raise HTTPException(403, detail="ideas_forbidden")
    if _ADMIN_SOURCE in ctx.units:
        raise HTTPException(503, detail="ideas_unavailable")
    return ctx


def _sources(request: Request, ctx: PortfolioContext) -> dict[str, str]:
    lang = request.app.state.service.lang(request.query_params.get("lang"))
    return {**ctx.units, _ADMIN_SOURCE: "Administration Factory" if lang == "fr" else "Factory Administration"}


def _store(request: Request) -> IdeasStore:
    return request.app.state.ideas


@router.get("")
def board(request: Request, access: Reader, q: Annotated[str, Query(max_length=120)] = "",
          category: Annotated[str, Query(max_length=32)] | None = None,
          bu: Annotated[str, Query(max_length=128)] | None = None,
          limit: Annotated[int, Query(ge=1, le=120)] | None = None, cursor: Cursor | None = None):
    ctx = _ideas_context(request, access)
    writable = _writable(ctx)
    sources = _sources(request, ctx)
    if (bu is not None and bu not in sources or category is not None and category not in _CATEGORIES
            or any(unicodedata.category(character).startswith("C") for character in q)):
        raise HTTPException(422, detail="invalid_ideas_request")
    query = _fold(q.strip())
    page_size = _VISIBLE_LIMIT if limit is None else limit
    key = request.app.state.service.booking_key
    page_scope = ["ideas", ctx.cursor_scope, request.app.state.service.lang(request.query_params.get("lang")),
                  query, category, bu, page_size, sorted(sources.items())]
    position = None
    if cursor is not None:
        position = decode_cursor(key, page_scope, cursor)
        if position is None or len(position) != 3:
            raise HTTPException(422, detail="invalid_ideas_cursor")
    items, totals, next_position = _store(request).page(
        sources, limit=page_size, query=query, category=category, bu=bu, position=position)
    total = totals.get(category, 0) if category is not None else sum(totals.values())
    return {"items": [{**item, "bu_label": sources[item["bu"]]} for item in items],
            "units": [{"id": unit, "label": sources[unit]} for unit in sorted(writable)],
            "permissions": {"create": bool(writable), "trace": ctx.global_admin}, "view": ctx.view,
            "total": total, "category_totals": totals, "truncated": next_position is not None,
            "next_cursor": encode_cursor(key, page_scope, next_position) if next_position else None}


@router.post("", status_code=201)
def create_idea(body: IdeaIn, request: Request, access: Writer):
    ctx = _ideas_context(request, access)
    writable = _writable(ctx)
    bu = body.bu
    if bu is None and writable:
        if len(writable) != 1:
            raise HTTPException(422, detail="ideas_bu_required")
        bu = next(iter(writable))
    if bu not in writable:
        raise HTTPException(403, detail="ideas_forbidden")
    subject = access.principal.user
    assert subject is not None  # Portfolio context requires the verified proxy subject.
    kind: AuthorKind = "agent" if ctx.agent_subject else "admin" if ctx.global_admin else "employee"
    card = _store(request).create(body, bu=bu, subject=subject, kind=kind)
    return {**card, "bu_label": _sources(request, ctx)[bu]}


@router.post("/{idea_id}/trace")
def trace_idea(idea_id: EntityID, _body: Input, request: Request, access: Writer):
    ctx = _ideas_context(request, access)
    if not ctx.global_admin:
        raise HTTPException(403, detail="ideas_forbidden")
    origin = _store(request).origin(idea_id, frozenset(_sources(request, ctx)))
    if origin is None:
        raise HTTPException(404, detail="idea_origin_unavailable")
    try:
        request.app.state.service.state.append_audit(
            replace(access.audit_context(), role="admin"), action="ideas.author_trace", target=f"idea:{idea_id}",
            require_intact=True,
        )
    except (OSError, StateError):
        raise HTTPException(503, detail="ideas_trace_unavailable") from None
    return {**origin, "view": ctx.view}
