"""Optional, transaction-coupled Workbench audit events and offline verification."""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from ._hashing import canonical, digest

GENESIS = "0" * 64
_MAX_RECORD_BYTES = 4096
_HASH = re.compile(r"[0-9a-f]{64}")
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z")
_CONTROL = frozenset({"journal.enabled", "journal.disabled"})
_OUTCOMES = {
    "journal.enabled": {"success"}, "journal.disabled": {"success"},
    "case.proposed": {"success"}, "case.revised": {"success"}, "case.withdrawn": {"success"},
    "case.blocked": {"blocked"},
    "decision.recorded": {"approved", "rejected", "changes_requested"},
    "execution.claimed": {"success"}, "execution.dispatch_authorized": {"success"},
    "execution.completed": {"success"},
    "execution.hold": {"blocked"}, "execution.unknown": {"unknown"},
    "crm.checkpoint": {"success"},
}
_REASONS = {
    "case.blocked": {"crm_conflict", "crm_invalid", "crm_incomplete"},
    "execution.hold": {"crm_reconciliation", "erp_readback_conflict"},
    "execution.unknown": {"erp_timeout"},
}
_REQUIRED = frozenset({"v", "seq", "ts", "event", "outcome", "prev_hash", "hash"})
_REFS = frozenset({"case_ref", "actor_ref", "source_ref", "request_id", "proposal_hash"})
_OPTIONAL = _REFS | {"revision", "channel", "reason_code"}
_CHANNELS = frozenset({"direct", "slack", "teams", "service"})
_REQUEST_ID: ContextVar[str | None] = ContextVar("workbench_journal_request", default=None)

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS cyber_events (
        seq INTEGER PRIMARY KEY CHECK (seq > 0), event TEXT NOT NULL,
        hash TEXT NOT NULL, record TEXT NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS cyber_events_control ON cyber_events(seq)
       WHERE event IN ('journal.enabled', 'journal.disabled')""",
    """CREATE TRIGGER IF NOT EXISTS cyber_events_no_update BEFORE UPDATE ON cyber_events
       BEGIN SELECT RAISE(ABORT, 'cyber journal is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS cyber_events_no_delete BEFORE DELETE ON cyber_events
       BEGIN SELECT RAISE(ABORT, 'cyber journal is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS cyber_events_sequential BEFORE INSERT ON cyber_events
       WHEN NEW.seq <> COALESCE((SELECT max(seq) + 1 FROM cyber_events), 1)
       BEGIN SELECT RAISE(ABORT, 'cyber journal requires next sequence'); END""",
)
_BOUNDED_COLUMNS = """seq, substr(event, 1, 48), substr(hash, 1, 65),
    CASE WHEN length(CAST(record AS BLOB)) <= 4096 THEN record END"""


class JournalError(ValueError):
    """A bounded diagnostic that carries no record contents or filesystem paths."""


@contextmanager
def journal_request(request_id: str) -> Iterator[None]:
    """Bind a trusted request ID, stored only as a workspace-scoped reference."""
    token = _REQUEST_ID.set(request_id)
    try:
        yield
    finally:
        _REQUEST_ID.reset(token)


def _reference(workspace: str, kind: str, value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 256:
        raise JournalError("invalid journal reference")
    try:
        return digest(["workbench-cyber-ref-v1", workspace, kind, value])
    except UnicodeError:
        raise JournalError("invalid journal reference") from None


def _workspace(connection: sqlite3.Connection) -> str:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workspace_settings'"
    ).fetchone()
    if table is None:
        raise JournalError("not a bound Workbench database")
    binding = connection.execute("SELECT workspace, target FROM workspace_settings WHERE id = 1").fetchone()
    if (binding is None or not isinstance(binding[0], str) or not 1 <= len(binding[0]) <= 128
            or not isinstance(binding[1], str) or not binding[1]):
        raise JournalError("not a bound Workbench database")
    return binding[0]


def _validated(row: tuple[Any, ...] | sqlite3.Row, *, expected_seq: int | None = None,
               prev_hash: str | None = None) -> dict[str, Any]:
    seq, event, stored_hash, raw = row
    try:
        record = json.loads(raw)
        if not isinstance(record, dict) or not _REQUIRED <= record.keys() <= _REQUIRED | _OPTIONAL:
            raise ValueError
        if (type(record["v"]) is not int or record["v"] != 1 or type(seq) is not int or seq < 1
                or type(record["seq"]) is not int or record["seq"] != seq or record["event"] != event
                or record["outcome"] not in _OUTCOMES.get(event, ()) or record["hash"] != stored_hash
                or (expected_seq is not None and seq != expected_seq)
                or (prev_hash is not None and record["prev_hash"] != prev_hash)):
            raise ValueError
        if any(not isinstance(record[key], str) or not _HASH.fullmatch(record[key])
               for key in ({"prev_hash", "hash"} | _REFS) & record.keys()):
            raise ValueError
        if not _TIMESTAMP.fullmatch(record["ts"]):
            raise ValueError
        dt.datetime.fromisoformat(record["ts"])
        case_event = event.startswith(("case.", "decision.", "execution."))
        case_fields = {"case_ref", "revision", "proposal_hash"}
        if case_event:
            if (not case_fields <= record.keys() or type(record["revision"]) is not int
                    or not 1 <= record["revision"] <= 2**53 - 1):
                raise ValueError
        elif case_fields & record.keys():
            raise ValueError
        if event == "decision.recorded":
            if record.get("channel") not in _CHANNELS or "actor_ref" not in record:
                raise ValueError
        elif "channel" in record:
            raise ValueError
        if event in _REASONS:
            if record.get("reason_code") not in _REASONS[event]:
                raise ValueError
        elif "reason_code" in record:
            raise ValueError
        if event == "crm.checkpoint" and "source_ref" not in record:
            raise ValueError
        if event in _CONTROL and record.keys() != _REQUIRED:
            raise ValueError
        if (canonical(record).decode() != raw
                or digest({key: value for key, value in record.items() if key != "hash"}) != stored_hash):
            raise ValueError
    except (TypeError, ValueError, RecursionError, OverflowError, UnicodeError):
        raise JournalError("invalid journal event or broken chain") from None
    return record


def _control_event(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        f"SELECT {_BOUNDED_COLUMNS} FROM cyber_events WHERE event IN ('journal.enabled', 'journal.disabled') "
        "ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    if row is not None:
        return _validated(row)["event"]
    if connection.execute("SELECT 1 FROM cyber_events LIMIT 1").fetchone() is not None:
        raise JournalError("missing journal configuration event")
    return None


def _append(connection: sqlite3.Connection, event: str, outcome: str, fields: dict[str, Any]) -> None:
    head = connection.execute(f"SELECT {_BOUNDED_COLUMNS} FROM cyber_events ORDER BY seq DESC LIMIT 1").fetchone()
    previous = _validated(head) if head is not None else None
    record = {"v": 1, "seq": previous["seq"] + 1 if previous else 1,
              "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
              "event": event, "outcome": outcome, "prev_hash": previous["hash"] if previous else GENESIS,
              **fields}
    record["hash"] = digest(record)
    raw = canonical(record)
    if len(raw) > _MAX_RECORD_BYTES:
        raise JournalError("journal event exceeds size limit")
    _validated((record["seq"], event, record["hash"], raw.decode()))
    connection.execute("INSERT INTO cyber_events(seq, event, hash, record) VALUES (?, ?, ?, ?)",
                       (record["seq"], event, record["hash"], raw.decode()))


def _setup(connection: sqlite3.Connection) -> None:
    for statement in _SCHEMA:
        connection.execute(statement)


def initialize_journal(connection: sqlite3.Connection, *, enable_if_unconfigured: bool) -> None:
    """Create the schema in the caller's transaction and respect an existing policy."""
    try:
        _setup(connection)
        if enable_if_unconfigured and _control_event(connection) is None:
            _append(connection, "journal.enabled", "success", {})
    except sqlite3.Error:
        raise JournalError("journal initialization failed") from None


def journal_enabled(connection: sqlite3.Connection) -> bool:
    try:
        return _control_event(connection) == "journal.enabled"
    except sqlite3.Error:
        raise JournalError("journal policy read failed") from None


def append_event(connection: sqlite3.Connection, event: str, outcome: str, *,
                 case: dict[str, Any] | None = None, actor: str | None = None, source: str | None = None,
                 channel: str | None = None, reason_code: str | None = None,
                 request_id: str | None = None) -> None:
    """Append a fixed-field event in the same transaction as its business state."""
    try:
        if not journal_enabled(connection):
            return
        workspace = _workspace(connection)
        fields: dict[str, Any] = {}
        if case is not None:
            fields.update(case_ref=_reference(workspace, "case", case["id"]), revision=case["revision"],
                          proposal_hash=case["proposal_hash"])
            source = case["source"]
        for key, kind, value in (("actor_ref", "actor", actor), ("source_ref", "source", source),
                                 ("request_id", "request", request_id or _REQUEST_ID.get())):
            if value is not None:
                fields[key] = _reference(workspace, kind, value)
        if channel is not None:
            fields["channel"] = channel
        if reason_code is not None:
            fields["reason_code"] = reason_code
        _append(connection, event, outcome, fields)
    except sqlite3.Error:
        raise JournalError("journal append failed") from None


def _open_database(path: Path, mode: str) -> sqlite3.Connection:
    connection = sqlite3.connect(Path(path).resolve().as_uri() + f"?mode={mode}", uri=True,
                                 timeout=5, isolation_level=None)
    try:
        connection.execute("PRAGMA trusted_schema = OFF")
    except BaseException:
        connection.close()
        raise
    return connection


def set_journal_enabled(path: Path, enabled: bool) -> bool:
    """Change the shared policy in an existing Workbench DB and audit the transition."""
    if type(enabled) is not bool:
        raise JournalError("invalid journal policy")
    try:
        with closing(_open_database(path, "rw")) as connection:
            connection.execute("BEGIN IMMEDIATE")
            _workspace(connection)
            _setup(connection)
            desired = "journal.enabled" if enabled else "journal.disabled"
            if _control_event(connection) == desired:
                return False
            _append(connection, desired, "success", {})
            connection.commit()
            return True
    except (sqlite3.Error, OSError, RuntimeError):
        raise JournalError("journal policy update failed") from None


def iter_verified_events(path: Path) -> Iterator[dict[str, Any]]:
    """Stream and validate a read-only SQLite snapshot from the journal genesis."""
    try:
        with closing(_open_database(path, "ro")) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            _workspace(connection)
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'cyber_events'"
            ).fetchone() is None:
                return
            prev_hash, enabled = GENESIS, False
            for seq, row in enumerate(connection.execute(
                f"SELECT {_BOUNDED_COLUMNS} FROM cyber_events ORDER BY seq"
            ), start=1):
                record = _validated(row, expected_seq=seq, prev_hash=prev_hash)
                if record["event"] in _CONTROL:
                    desired = record["event"] == "journal.enabled"
                    if seq > 1 and desired == enabled:
                        raise JournalError("invalid journal policy transition")
                    enabled = desired
                elif not enabled:
                    raise JournalError("journal event outside enabled interval")
                prev_hash = record["hash"]
                yield record
    except (sqlite3.Error, OSError, RuntimeError):
        raise JournalError("journal read failed") from None
