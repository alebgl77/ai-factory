"""SQLite state and isolated ERP writes for the workbench sandbox."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from ._hashing import canonical, digest


class WorkbenchError(Exception):
    """Public error code without request data."""

    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.status = status


SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    subject TEXT NOT NULL,
    status TEXT NOT NULL,
    record TEXT NOT NULL,
    UNIQUE (source, subject)
);
CREATE INDEX IF NOT EXISTS cases_queue ON cases(status, id);
CREATE TABLE IF NOT EXISTS events (
    source TEXT NOT NULL,
    id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    case_id TEXT NOT NULL REFERENCES cases(id),
    PRIMARY KEY (source, id)
);
CREATE TABLE IF NOT EXISTS decisions (
    case_id TEXT NOT NULL REFERENCES cases(id),
    revision INTEGER NOT NULL,
    actor TEXT NOT NULL,
    proposal_hash TEXT NOT NULL,
    decision TEXT NOT NULL,
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (case_id, revision)
);
CREATE TABLE IF NOT EXISTS approval_provenance (
    issuer TEXT NOT NULL,
    request_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    metadata TEXT NOT NULL,
    PRIMARY KEY (issuer, request_id),
    UNIQUE (case_id, revision),
    FOREIGN KEY (case_id, revision) REFERENCES decisions(case_id, revision)
);
CREATE TABLE IF NOT EXISTS feedback (
    case_id TEXT NOT NULL REFERENCES cases(id),
    actor TEXT NOT NULL,
    rating TEXT NOT NULL,
    comment TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (case_id, actor)
);
CREATE TABLE IF NOT EXISTS erp_drafts (
    external_ref TEXT PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    action_hash TEXT NOT NULL,
    fields TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crm_checkpoints (
    source TEXT PRIMARY KEY,
    start_at INTEGER NOT NULL,
    until_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS crm_revisions (
    source TEXT NOT NULL,
    subject TEXT NOT NULL,
    record_version INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    PRIMARY KEY (source, subject)
);
"""


_LEGACY_TABLES = frozenset({"cases", "events", "decisions", "feedback", "erp_drafts"})
_CRM_TABLE_PROBES = {
    "crm_checkpoints": "SELECT 1 FROM crm_checkpoints LIMIT 1",
    "crm_revisions": "SELECT 1 FROM crm_revisions LIMIT 1",
}


class Store:
    def __init__(self, path: Path, workspace: str = "demo", target: str = "sandbox-erp") -> None:
        self.path = Path(path)
        self._binding = (workspace, target)
        self._initialized = False
        self._initialization_lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        if self._initialized:
            return
        with self._initialization_lock:
            if self._initialized:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                tables = {row["name"] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT GLOB 'sqlite_*'"
                )}
                if "workspace_settings" in tables:
                    self._check_binding(connection)
                else:
                    has_crm_state = any(table in tables and connection.execute(probe).fetchone() is not None
                                        for table, probe in _CRM_TABLE_PROBES.items())
                    if tables and (not tables.difference(_CRM_TABLE_PROBES) <= _LEGACY_TABLES
                                   or self._binding != ("demo", "sandbox-erp") or has_crm_state):
                        raise WorkbenchError("workspace_binding_required", 409)
                    connection.execute(
                        "CREATE TABLE workspace_settings (id INTEGER PRIMARY KEY CHECK (id = 1), "
                        "workspace TEXT NOT NULL, target TEXT NOT NULL)"
                    )
                    connection.execute(
                        "INSERT INTO workspace_settings(id,workspace,target) VALUES(1,?,?)", self._binding,
                    )
                # executescript would commit before schema creation, leaving a partially bound store.
                for statement in SCHEMA.strip().removesuffix(";").split(";"):
                    connection.execute(statement)
                connection.commit()
                try:
                    connection.execute("PRAGMA journal_mode = WAL")
                except sqlite3.OperationalError as error:
                    # Journal mode can only change outside a transaction. Another
                    # process may already be using the store after the binding commits.
                    if error.sqlite_errorcode & 0xFF not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                        raise
            self._initialized = True

    def _check_binding(self, connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT workspace,target FROM workspace_settings WHERE id = 1").fetchone()
        if row is None or row["workspace"] != self._binding[0]:
            raise WorkbenchError("workspace_mismatch", 409)
        if row["target"] != self._binding[1]:
            raise WorkbenchError("connector_target_mismatch", 409)

    @contextmanager
    def transaction(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        self._initialize()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                self._check_binding(connection)
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def load_case(connection: sqlite3.Connection, case_id: str) -> dict[str, Any]:
        row = connection.execute("SELECT record FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            raise WorkbenchError("case_not_found", 404)
        return json.loads(row["record"])

    @staticmethod
    def put_case(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
        connection.execute(
            """INSERT INTO cases(id, source, subject, status, record) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET status = excluded.status, record = excluded.record""",
            (record["id"], record["source"], record["subject"], record["status"], canonical(record).decode()),
        )

    def create_draft(self, external_ref: str, fields: dict[str, Any]) -> dict[str, str]:
        """Sandbox adapter. Its transaction commits independently of the workbench receipt."""
        action_hash = digest(fields)
        draft_id = "draft-" + digest(external_ref)[:20]
        with self.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO erp_drafts(external_ref,id,action_hash,fields) VALUES (?,?,?,?)",
                (external_ref, draft_id, action_hash, canonical(fields).decode()),
            )
            row = connection.execute(
                "SELECT id, action_hash FROM erp_drafts WHERE external_ref = ?", (external_ref,)
            ).fetchone()
            if row is None or row["action_hash"] != action_hash:
                raise WorkbenchError("draft_conflict", 409)
            return {"id": row["id"], "external_ref": external_ref, "system": "sandbox-erp", "state": "draft"}

    def draft_count(self) -> int:
        with self.transaction(write=False) as connection:
            return connection.execute("SELECT count(*) FROM erp_drafts").fetchone()[0]
