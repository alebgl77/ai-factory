"""Transactional portfolio state. HTTP authorization is kept at the boundary."""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_DB_NAME = "portfolio.sqlite3"
_GOAL_LIMIT = 100
_PROJECT_LIMIT = 200
_TASK_LIMIT = 200
_HISTORY_LIMIT = 20
_CHILD_LIMIT = 4
_CLOSED_PROJECTS = frozenset({"done", "archived"})
_ASSIGNABLE = frozenset({"planned", "ready", "blocked"})
_TERMINAL_TASKS = frozenset({"done", "cancelled"})

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS goals (id TEXT PRIMARY KEY, payload TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id), payload TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), unit TEXT NOT NULL,
        parent_task_id TEXT REFERENCES tasks(id),
        assignee_kind TEXT, assignee_id TEXT, status TEXT NOT NULL, updated_at TEXT NOT NULL, start_policy TEXT,
        payload TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS tasks_unit_order ON tasks(unit, updated_at DESC, id)",
    """CREATE INDEX IF NOT EXISTS tasks_assignee_order
        ON tasks(unit, assignee_kind, assignee_id, updated_at DESC, id)""",
    "CREATE INDEX IF NOT EXISTS tasks_project ON tasks(project_id, unit, status)",
    "CREATE INDEX IF NOT EXISTS tasks_parent ON tasks(parent_task_id, status)",
    """CREATE TABLE IF NOT EXISTS workbench_authors (
        task_id TEXT PRIMARY KEY REFERENCES tasks(id), aliases TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS workbench_reviews (
        seq INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), task_revision INTEGER NOT NULL,
        unit TEXT NOT NULL, proposal TEXT, case_id TEXT UNIQUE, case_revision INTEGER, proposal_hash TEXT,
        applied_at TEXT, UNIQUE (task_id, task_revision)
    )""",
    """CREATE INDEX IF NOT EXISTS workbench_pending ON workbench_reviews(seq)
        WHERE applied_at IS NULL""",
    """CREATE TABLE IF NOT EXISTS task_dependencies (
        task_id TEXT NOT NULL REFERENCES tasks(id), depends_on TEXT NOT NULL REFERENCES tasks(id),
        PRIMARY KEY (task_id, depends_on)
    )""",
    """CREATE TABLE IF NOT EXISTS task_history (
        task_id TEXT NOT NULL REFERENCES tasks(id), revision INTEGER NOT NULL,
        action TEXT NOT NULL, status TEXT NOT NULL, actor_ref TEXT NOT NULL,
        actor TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (task_id, revision)
    )""",
    """CREATE TABLE IF NOT EXISTS portfolio_runner_claims (
        task_id TEXT PRIMARY KEY REFERENCES tasks(id), attempt TEXT NOT NULL,
        revision INTEGER NOT NULL, lease_until TEXT NOT NULL,
        parent_start_revision INTEGER NOT NULL, parent_agent_id TEXT NOT NULL, project_revision INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS portfolio_runner_leases ON portfolio_runner_claims(lease_until)",
    """CREATE INDEX IF NOT EXISTS portfolio_parent_starts ON task_history(task_id, revision DESC)
        WHERE action = 'start'""",
    """CREATE INDEX IF NOT EXISTS portfolio_runner_queue ON tasks(assignee_id, status, unit, updated_at, id)
        WHERE assignee_kind = 'agent' AND parent_task_id IS NOT NULL""",
)

_BLOCKERS = """NOT EXISTS (
    SELECT 1 FROM task_dependencies AS dependency
    JOIN tasks AS predecessor ON predecessor.id = dependency.depends_on
    WHERE dependency.task_id = task.id AND predecessor.status != 'done'
)"""


class PortfolioError(Exception):
    """A safe business error. Authorization denials come from the caller."""

    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class PortfolioActor:
    ref: str
    label: str


@dataclass(frozen=True)
class PortfolioReview:
    seq: int
    task_id: str
    task_revision: int
    unit: str
    proposal: str | None
    case_id: str | None
    case_revision: int | None
    proposal_hash: str | None

    @property
    def subject(self) -> str:
        return f"portfolio/{self.task_id}/{self.task_revision}"


def _encode(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _check_revision(current: dict[str, Any], expected: int) -> None:
    if current["revision"] != expected:
        raise PortfolioError("revision_conflict")


def _managed_fields(data: dict[str, Any]) -> dict[str, Any]:
    return {"title": data["title"], "description": data.get("description", ""), "due_on": data.get("due_on")}


def _task_payload(row: sqlite3.Row, policy_fingerprint: str | None) -> dict[str, Any]:
    task = json.loads(row["payload"])
    task["start_authorized"] = task["start_authorized"] and row["start_policy"] == policy_fingerprint
    return task


class PortfolioStore:
    def __init__(self, state_dir: str | Path, clock: Callable[[], dt.datetime] | None = None) -> None:
        self.path = Path(state_dir) / _DB_NAME
        self.clock = clock or (lambda: dt.datetime.now(dt.UTC))

    def _now(self) -> str:
        return self.clock().astimezone(dt.UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")

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
                for statement in _SCHEMA:
                    db.execute(statement)
            elif db.execute("SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'goals'").fetchone() is None:
                # A first writer can have created the file before committing the schema.
                yield None
                return
            yield db

    def snapshot(self, *, managed_units: frozenset[str],
                 own_assignments: tuple[tuple[str, str, str], ...],
                 policy_fingerprint: str | None = None) -> dict[str, Any]:
        """Read global project summaries and only the authorized task partitions."""
        with self._transaction(write=False) as db:
            if db is None:
                return {"goals": [], "projects": [], "tasks": [], "truncated": False}
            goals = [json.loads(row[0]) for row in db.execute("SELECT payload FROM goals ORDER BY id")]
            projects = [json.loads(row[0]) for row in db.execute("SELECT payload FROM projects ORDER BY id")]
            rows = db.execute(f"""
                WITH own AS (
                    SELECT task.id, task.updated_at, task.unit, task.project_id FROM json_each(?) AS assignment
                    JOIN tasks AS task ON task.unit = json_extract(assignment.value, '$[0]')
                      AND task.assignee_kind = json_extract(assignment.value, '$[1]')
                      AND task.assignee_id = json_extract(assignment.value, '$[2]')
                ), visible AS (
                    SELECT id, updated_at FROM tasks WHERE unit IN (SELECT value FROM json_each(?))
                    UNION SELECT id, updated_at FROM own
                    UNION SELECT child.id, child.updated_at FROM own
                          JOIN tasks AS child ON child.parent_task_id = own.id
                            AND child.unit = own.unit AND child.project_id = own.project_id
                    ORDER BY updated_at DESC, id LIMIT ?
                ) SELECT task.id, payload, start_policy, {_BLOCKERS} AS dependencies_ready
                  FROM visible JOIN tasks AS task ON task.id = visible.id
                  ORDER BY visible.updated_at DESC, visible.id
            """, (_encode(own_assignments), _encode(sorted(managed_units)), _TASK_LIMIT + 1)).fetchall()
            tasks = [dict(_task_payload(row, policy_fingerprint), dependencies_ready=bool(row["dependencies_ready"]),
                          history=self._history(db, row["id"])) for row in rows[:_TASK_LIMIT]]
            return {"goals": goals, "projects": projects, "tasks": tasks, "truncated": len(rows) > _TASK_LIMIT}

    def _created(self, object_id: str, data: dict[str, Any]) -> dict[str, Any]:
        now = self._now()
        return {**data, "id": object_id, "revision": 1, "created_at": now, "updated_at": now}

    def _revised(self, current: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
        return {**current, **changes, "revision": current["revision"] + 1, "updated_at": self._now()}

    @staticmethod
    def _capacity(db: sqlite3.Connection, table: str, object_id: str, limit: int) -> None:
        if table == "goals":
            exists = db.execute("SELECT 1 FROM goals WHERE id = ?", (object_id,)).fetchone()
            count = db.execute("SELECT COUNT(*) FROM goals").fetchone()[0]
        else:
            exists = db.execute("SELECT 1 FROM projects WHERE id = ?", (object_id,)).fetchone()
            count = db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        if exists:
            raise PortfolioError("duplicate_id")
        if count >= limit:
            raise PortfolioError("portfolio_capacity")

    @staticmethod
    def _goal(db: sqlite3.Connection, goal_id: str) -> dict[str, Any]:
        row = db.execute("SELECT payload FROM goals WHERE id = ?", (goal_id,)).fetchone()
        if row is None:
            raise PortfolioError("goal_not_found", 404)
        return json.loads(row[0])

    @staticmethod
    def _project(db: sqlite3.Connection, project_id: str) -> dict[str, Any]:
        row = db.execute("SELECT payload FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise PortfolioError("project_not_found", 404)
        return json.loads(row[0])

    def create_goal(self, goal_id: str, data: dict[str, Any], actor: PortfolioActor) -> dict[str, Any]:
        with self._transaction(write=True) as db:
            assert db is not None
            self._capacity(db, "goals", goal_id, _GOAL_LIMIT)
            goal = self._created(goal_id, {**_managed_fields(data), "metric": data["metric"],
                                            "target": data["target"], "current": data.get("current"),
                                            "direction": data.get("direction", "at_least")})
            db.execute("INSERT INTO goals (id, payload) VALUES (?, ?)", (goal_id, _encode(goal)))
            return goal

    def update_goal(self, goal_id: str, revision: int, changes: dict[str, Any],
                    actor: PortfolioActor) -> dict[str, Any]:
        with self._transaction(write=True) as db:
            assert db is not None
            current = self._goal(db, goal_id)
            _check_revision(current, revision)
            fields = {"title", "description", "due_on", "metric", "target", "current", "direction"}
            goal = self._revised(current, {field: changes[field] for field in fields & changes.keys()})
            db.execute("UPDATE goals SET payload = ? WHERE id = ?", (_encode(goal), goal_id))
            return goal

    def create_project(self, project_id: str, data: dict[str, Any], actor: PortfolioActor) -> dict[str, Any]:
        with self._transaction(write=True) as db:
            assert db is not None
            self._capacity(db, "projects", project_id, _PROJECT_LIMIT)
            self._goal(db, data["goal_id"])
            units = data["unit_ids"]
            if data["owner_unit"] not in units or len(set(units)) != len(units):
                raise PortfolioError("invalid_project_units", 422)
            project = self._created(project_id, {
                **_managed_fields(data), "goal_id": data["goal_id"], "owner_unit": data["owner_unit"],
                "unit_ids": units, "usecase_id": data.get("usecase_id"), "state": data.get("state", "planned"),
            })
            db.execute("INSERT INTO projects (id, goal_id, payload) VALUES (?, ?, ?)",
                       (project_id, project["goal_id"], _encode(project)))
            return project

    def update_project(self, project_id: str, revision: int, changes: dict[str, Any],
                       actor: PortfolioActor) -> dict[str, Any]:
        with self._transaction(write=True) as db:
            assert db is not None
            current = self._project(db, project_id)
            _check_revision(current, revision)
            if changes.get("state") == "done" and db.execute(
                "SELECT 1 FROM tasks WHERE project_id = ? AND status NOT IN ('done', 'cancelled') LIMIT 1",
                (project_id,),
            ).fetchone():
                raise PortfolioError("project_has_open_tasks")
            fields = {"title", "description", "due_on", "state"}
            project = self._revised(current, {field: changes[field] for field in fields & changes.keys()})
            db.execute("UPDATE projects SET payload = ? WHERE id = ?", (_encode(project), project_id))
            return project

    @staticmethod
    def _task(db: sqlite3.Connection, task_id: str, policy_fingerprint: str | None) -> dict[str, Any]:
        row = db.execute("SELECT payload, start_policy FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise PortfolioError("task_not_found", 404)
        return _task_payload(row, policy_fingerprint)

    @staticmethod
    def _task_dependencies(db: sqlite3.Connection, task: dict[str, Any]) -> None:
        dependencies = task["dependency_ids"]
        if task["id"] in dependencies:
            raise PortfolioError("dependency_cycle")
        local = db.execute("""
            SELECT COUNT(*) FROM tasks WHERE project_id = ? AND unit = ?
              AND id IN (SELECT value FROM json_each(?))
        """, (task["project_id"], task["unit"], _encode(dependencies))).fetchone()[0]
        if local != len(dependencies):
            raise PortfolioError("dependency_unavailable")
        if db.execute("""
            WITH RECURSIVE predecessors(id) AS (
                SELECT value FROM json_each(?)
                UNION
                SELECT depends_on FROM task_dependencies JOIN predecessors ON task_id = predecessors.id
                UNION
                SELECT child.id FROM tasks AS child JOIN predecessors ON child.parent_task_id = predecessors.id
            ) SELECT 1 FROM predecessors WHERE id = ? LIMIT 1
        """, (_encode(dependencies), task["id"])).fetchone():
            raise PortfolioError("dependency_cycle")
        db.execute("DELETE FROM task_dependencies WHERE task_id = ?", (task["id"],))
        db.executemany("INSERT INTO task_dependencies (task_id, depends_on) VALUES (?, ?)",
                       ((task["id"], dep_id) for dep_id in dependencies))

    @staticmethod
    def _dependencies_ready(db: sqlite3.Connection, task_id: str) -> bool:
        return bool(db.execute(f"SELECT {_BLOCKERS} FROM tasks AS task WHERE id = ?", (task_id,)).fetchone()[0])

    @staticmethod
    def _append_history(db: sqlite3.Connection, task: dict[str, Any], action: str, actor: PortfolioActor) -> None:
        db.execute("""
            INSERT INTO task_history (task_id, revision, action, status, actor_ref, actor, at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (task["id"], task["revision"], action, task["status"], actor.ref, actor.label, task["updated_at"]))

    @staticmethod
    def _history(db: sqlite3.Connection, task_id: str) -> list[dict[str, Any]]:
        # The composite history key allows each bounded read to stop at its limit.
        return [dict(event) for event in db.execute("""
            SELECT action, status, actor, revision, at FROM task_history
            WHERE task_id = ? ORDER BY revision DESC LIMIT ?
        """, (task_id, _HISTORY_LIMIT))]

    def _project_task(self, db: sqlite3.Connection, task: dict[str, Any]) -> dict[str, Any]:
        return {**task, "dependencies_ready": self._dependencies_ready(db, task["id"]),
                "history": self._history(db, task["id"])}

    @staticmethod
    def _write_task(db: sqlite3.Connection, task: dict[str, Any], start_policy: str | None = None) -> None:
        assignee = task["assignee"] or {}
        db.execute("""
            UPDATE tasks SET assignee_kind = ?, assignee_id = ?, status = ?, updated_at = ?, start_policy = ?,
                             payload = ? WHERE id = ?
        """, (assignee.get("kind"), assignee.get("id"), task["status"], task["updated_at"],
              start_policy, _encode(task), task["id"]))

    def create_task(self, task_id: str, data: dict[str, Any], actor: PortfolioActor) -> dict[str, Any]:
        with self._transaction(write=True) as db:
            assert db is not None
            project = self._project(db, data["project_id"])
            if data["unit"] not in project["unit_ids"]:
                raise PortfolioError("project_not_found", 404)
            if project["state"] in _CLOSED_PROJECTS:
                raise PortfolioError("project_closed")
            return self._insert_task(db, task_id, data, actor)

    def _insert_task(self, db: sqlite3.Connection, task_id: str, data: dict[str, Any], actor: PortfolioActor,
                     *, parent_task_id: str | None = None,
                     assignee: dict[str, str] | None = None) -> dict[str, Any]:
        if db.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone():
            raise PortfolioError("duplicate_id")
        task = self._created(task_id, {
            **_managed_fields(data), "project_id": data["project_id"], "unit": data["unit"],
            "priority": data.get("priority", "normal"), "execution": data["execution"],
            "technical": data.get("technical", False), "dependency_ids": data.get("dependency_ids", []),
            "status": "ready" if assignee else "planned", "assignee": assignee, "evidence": [],
            "start_authorized": False, "parent_task_id": parent_task_id,
            "delegation": data["delegation"] if parent_task_id else None, "tokens": None,
        })
        binding = assignee or {}
        db.execute("""
            INSERT INTO tasks (id, project_id, unit, parent_task_id, assignee_kind, assignee_id,
                               status, updated_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (task_id, task["project_id"], task["unit"], parent_task_id, binding.get("kind"), binding.get("id"),
              task["status"], task["updated_at"], _encode(task)))
        if task["dependency_ids"]:
            self._task_dependencies(db, task)
        self._append_history(db, task, "create", actor)
        return self._project_task(db, task)

    def delegate_task(self, parent_id: str, revision: int, child_id: str, data: dict[str, Any],
                      actor: PortfolioActor,
                      authorize: Callable[[dict[str, Any], Mapping[str, int]], str]) -> dict[str, Any]:
        with self._transaction(write=True) as db:
            assert db is not None
            parent = self._task(db, parent_id, None)
            child_load = dict(db.execute("""
                SELECT assignee_id, COUNT(*) FROM tasks WHERE parent_task_id = ? GROUP BY assignee_id
            """, (parent_id,)).fetchall())
            child_count = sum(child_load.values())
            selected_agent = authorize(parent, child_load)
            self._ensure_changeable(db, parent, revision)
            binding = parent["assignee"] or {}
            if parent["parent_task_id"] or parent["status"] != "in_progress" or binding.get("kind") != "agent":
                raise PortfolioError("invalid_transition")
            if not selected_agent or selected_agent == binding["id"]:
                raise PortfolioError("assignee_incompatible", 422)
            if child_count >= _CHILD_LIMIT:
                raise PortfolioError("delegation_capacity")
            child = self._insert_task(db, child_id, {
                **_managed_fields(data), "priority": data.get("priority", parent["priority"]),
                "project_id": parent["project_id"], "unit": parent["unit"], "technical": parent["technical"],
                "execution": "read_only" if parent["execution"] == "read_only" else "prepare",
                "delegation": data["delegation"],
            }, actor, parent_task_id=parent_id, assignee={"kind": "agent", "id": selected_agent})
            parent = self._revised(parent, {})
            self._write_task(db, parent)
            self._append_history(db, parent, "delegate_task", actor)
            return {"parent": self._project_task(db, parent), "task": child}

    def _task_for_change(self, db: sqlite3.Connection, task_id: str, revision: int,
                         authorize: Callable[[dict[str, Any]], None],
                         policy_fingerprint: str | None = None) -> dict[str, Any]:
        current = self._task(db, task_id, policy_fingerprint)
        # Resolve authority against the row inside the same write transaction. An
        # inaccessible task must not expose its current revision or parent status.
        authorize(current)
        self._ensure_changeable(db, current, revision)
        return current

    def _ensure_changeable(self, db: sqlite3.Connection, current: dict[str, Any], revision: int) -> None:
        _check_revision(current, revision)
        if self._project(db, current["project_id"])["state"] in _CLOSED_PROJECTS:
            raise PortfolioError("project_closed")

    def update_task(self, task_id: str, revision: int, changes: dict[str, Any], actor: PortfolioActor,
                    authorize: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        with self._transaction(write=True) as db:
            assert db is not None
            current = self._task_for_change(db, task_id, revision, authorize)
            if current["status"] not in {"planned", "ready"}:
                raise PortfolioError("invalid_transition")
            fields = {"title", "description", "due_on", "priority", "dependency_ids"}
            task = self._revised(current, {"start_authorized": False,
                                           **{field: changes[field] for field in fields & changes.keys()}})
            if "dependency_ids" in changes:
                self._task_dependencies(db, task)
            self._write_task(db, task)
            self._append_history(db, task, "update", actor)
            return self._project_task(db, task)

    def command_task(self, task_id: str, revision: int, command: dict[str, Any], actor: PortfolioActor,
                     authorize: Callable[[dict[str, Any]], None], *,
                     policy_fingerprint: str | None = None,
                     workbench_aliases: tuple[str, ...] | None = None) -> dict[str, Any]:
        with self._transaction(write=True) as db:
            assert db is not None
            current = self._task_for_change(db, task_id, revision, authorize, policy_fingerprint)
            action = command["action"]
            if action in {"assign", "unblock", "submit", "report_usage"} and db.execute(
                "SELECT 1 FROM portfolio_runner_claims WHERE task_id = ?", (task_id,)
            ).fetchone():
                raise PortfolioError("portfolio_runner_claimed")
            changes = self._transition(db, current, action, command, actor)
            if not changes:
                return self._project_task(db, current)
            task = self._revised(current, changes)
            self._write_task(db, task, policy_fingerprint if action == "authorize_start" else None)
            self._append_history(db, task, action, actor)
            if workbench_aliases is not None and task["execution"] == "sensitive":
                self._record_workbench_transition(db, task, action, workbench_aliases)
            return self._project_task(db, task)

    @staticmethod
    def _record_workbench_transition(db: sqlite3.Connection, task: dict[str, Any], action: str,
                                     aliases: tuple[str, ...]) -> None:
        task_id = task["id"]
        if action == "submit" and task["status"] == "review":
            db.execute("""INSERT INTO workbench_authors (task_id, aliases) VALUES (?, ?)
                          ON CONFLICT (task_id) DO UPDATE SET aliases = excluded.aliases""",
                       (task_id, _encode(aliases)))
        elif action == "request_changes":
            db.execute("DELETE FROM workbench_authors WHERE task_id = ?", (task_id,))
        elif task["status"] == "workbench":
            if action == "approve":
                author = db.execute("SELECT aliases FROM workbench_authors WHERE task_id = ?", (task_id,)).fetchone()
                if author is None:
                    raise PortfolioError("workbench_submitter_required")
                aliases = tuple(sorted(set(aliases).union(json.loads(author["aliases"]))))
                db.execute("DELETE FROM workbench_authors WHERE task_id = ?", (task_id,))
            if not aliases:
                raise PortfolioError("workbench_submitter_required")
            proposal = {
                "title": task["title"],
                "reason": task["description"][:1000].strip() or "Review required for this sensitive portfolio task.",
                "evidence": [f"{item['kind']} · {item['label']} · {item['ref']}" for item in task["evidence"]],
                "exclusions": aliases,
            }
            db.execute("""INSERT INTO workbench_reviews (task_id, task_revision, unit, proposal)
                          VALUES (?, ?, ?, ?)""", (task_id, task["revision"], task["unit"], _encode(proposal)))

    def pending_workbench_reviews(self, *, linked: bool, after: int, limit: int) -> list[PortfolioReview]:
        with self._transaction(write=False) as db:
            if db is None or db.execute(
                "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'workbench_reviews'",
            ).fetchone() is None:
                return []
            rows = db.execute("""
                SELECT seq, task_id, task_revision, unit, proposal, case_id, case_revision, proposal_hash
                FROM workbench_reviews
                WHERE applied_at IS NULL AND seq > ? AND (case_id IS NOT NULL) = ?
                ORDER BY seq LIMIT ?
            """, (after, linked, limit))
            return [PortfolioReview(**dict(row)) for row in rows]

    def bind_workbench_review(self, review: PortfolioReview, binding: tuple[str, int, str]) -> None:
        with self._transaction(write=True) as db:
            assert db is not None
            db.execute("""
                UPDATE workbench_reviews SET case_id = ?, case_revision = ?, proposal_hash = ?, proposal = NULL
                WHERE seq = ? AND task_id = ? AND task_revision = ? AND unit = ? AND case_id IS NULL
            """, (*binding, review.seq, review.task_id, review.task_revision, review.unit))

    def apply_workbench_receipt(self, review: PortfolioReview, decision: str, decided_at: str) -> bool:
        statuses = {"approve": "done", "reject": "cancelled", "request_changes": "ready"}
        if decision not in statuses or review.case_id is None:
            return False
        with self._transaction(write=True) as db:
            assert db is not None
            if not db.execute("""
                SELECT 1 FROM workbench_reviews
                WHERE seq = ? AND task_id = ? AND task_revision = ? AND unit = ? AND case_id = ?
                  AND case_revision = ? AND proposal_hash = ? AND applied_at IS NULL
            """, (review.seq, review.task_id, review.task_revision, review.unit,
                  review.case_id, review.case_revision, review.proposal_hash)).fetchone():
                return False
            current = self._task(db, review.task_id, None)
            if (current["revision"] != review.task_revision or current["unit"] != review.unit
                    or current["status"] != "workbench" or current["execution"] != "sensitive"):
                return False
            changes: dict[str, Any] = {
                "status": statuses[decision], "start_authorized": False,
                "workbench_decision": {"outcome": decision, "at": decided_at},
            }
            if decision == "request_changes":
                changes["evidence"] = []
            task = self._revised(current, changes)
            self._write_task(db, task)
            self._append_history(db, task, "workbench_" + decision, PortfolioActor("workbench", "Workbench"))
            db.execute("UPDATE workbench_reviews SET applied_at = ? WHERE seq = ?", (self._now(), review.seq))
            return True

    def _transition(self, db: sqlite3.Connection, task: dict[str, Any], action: str,
                    command: dict[str, Any], actor: PortfolioActor) -> dict[str, Any]:
        status = task["status"]
        if action == "assign" and status in _ASSIGNABLE:
            assignee = command.get("assignee")
            if assignee is None:
                raise PortfolioError("assignee_required", 422)
            expected_kind = "employee" if task["execution"] == "human" else "agent"
            if assignee["kind"] != expected_kind:
                raise PortfolioError("assignee_incompatible", 422)
            if expected_kind == "agent" and db.execute("""
                SELECT 1 FROM tasks WHERE assignee_kind = 'agent' AND assignee_id = ?
                  AND (id = ? OR parent_task_id = ?) LIMIT 1
            """, (assignee["id"], task["parent_task_id"], task["id"])).fetchone():
                raise PortfolioError("assignee_incompatible", 422)
            tokens = task["tokens"] if assignee == task["assignee"] else None
            return {"status": "ready", "assignee": assignee, "evidence": [], "start_authorized": False,
                    "tokens": tokens, "runner_result": None, "runner_error": None,
                    "runner_started_at": None, "runner_finished_at": None}
        if action == "authorize_start" and status == "ready":
            if (task["assignee"] or {}).get("kind") != "agent" or task["start_authorized"]:
                raise PortfolioError("invalid_transition")
            return {"start_authorized": True}
        if action == "start" and status == "ready":
            if task["assignee"] is None or not self._dependencies_ready(db, task["id"]):
                raise PortfolioError("dependencies_incomplete")
            return {"status": "in_progress", "start_authorized": False}
        if action == "block" and status == "in_progress":
            return {"status": "blocked"}
        if action == "unblock" and status == "blocked":
            return {"status": "ready", "start_authorized": False, "runner_error": None}
        if action == "report_usage" and status == "in_progress":
            if command.get("tokens") is None:
                raise PortfolioError("token_usage_required", 422)
            tokens = self._token_usage(task, command["tokens"])
            return {"tokens": tokens} if tokens != task["tokens"] else {}
        if action == "submit" and status == "in_progress":
            if db.execute("""
                SELECT 1 FROM tasks WHERE parent_task_id = ? AND status NOT IN ('done', 'cancelled') LIMIT 1
            """, (task["id"],)).fetchone():
                raise PortfolioError("delegated_tasks_incomplete")
            next_status = "done"
            if task["technical"]:
                next_status = "review"
            elif task["execution"] == "sensitive":
                next_status = "workbench"
            tokens = self._token_usage(task, command.get("tokens"))
            return {"status": next_status, "evidence": command.get("evidence", []), "tokens": tokens}
        if action in {"approve", "request_changes"} and status == "review":
            submitted = db.execute("""
                SELECT actor_ref FROM task_history WHERE task_id = ? AND action = 'submit'
                ORDER BY revision DESC LIMIT 1
            """, (task["id"],)).fetchone()
            if submitted is None or submitted["actor_ref"] == actor.ref:
                raise PortfolioError("separation_of_duties", 403)
            if action == "request_changes":
                return {"status": "ready", "start_authorized": False}
            return {"status": "workbench" if task["execution"] == "sensitive" else "done"}
        if action == "cancel" and task["parent_task_id"] and status not in _TERMINAL_TASKS:
            return {"status": "cancelled", "start_authorized": False}
        raise PortfolioError("invalid_transition")

    @staticmethod
    def _token_usage(task: dict[str, Any], tokens: dict[str, int] | None) -> dict[str, int] | None:
        current = task["tokens"]
        if tokens is None:
            return current
        if (task["assignee"] or {}).get("kind") != "agent":
            raise PortfolioError("portfolio_agent_tokens_only", 403)
        if current is not None and any(tokens[key] < current[key] for key in ("input", "output")):
            raise PortfolioError("token_usage_regressed")
        if current is not None and current.get("source") in {"provider_reported", "mixed_reported"} and "source" not in tokens:
            if all(tokens[key] == current[key] for key in ("input", "output")):
                return current
            return {**tokens, "source": "mixed_reported"}
        return tokens
