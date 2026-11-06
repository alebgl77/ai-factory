"""Private aggregate Shadow AI Observatory storage and governance."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import hmac
import json
import secrets
import sqlite3
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable

from .security import Principal

MAX_PERIOD_ROWS = 200
MAX_PENDING_PROPOSALS = 500
MAX_DECISIONS = 5_000
MAX_POLICIES = 500
USAGE_PRIORITY = ("usage", "observation", "inventory")
EVIDENCE_MAX_AGE = dt.timedelta(days=7)
PENDING_MAX_AGE = dt.timedelta(days=90)
DECISIONS_MAX_AGE = dt.timedelta(days=365)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK(id = 1), privacy_threshold INTEGER NOT NULL, actor_key BLOB NOT NULL,
    journal_seq INTEGER NOT NULL DEFAULT 0, journal_hash TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY CHECK(id = 1), window_start TEXT NOT NULL, window_end TEXT NOT NULL,
    events INTEGER, unique_users INTEGER
);
CREATE TABLE IF NOT EXISTS periods (
    period TEXT PRIMARY KEY, team_usage TEXT NOT NULL, adoption TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS policies (
    tool TEXT PRIMARY KEY, status TEXT NOT NULL, alternative TEXT, revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY, tool TEXT NOT NULL, status TEXT NOT NULL, alternative TEXT,
    expected_revision INTEGER NOT NULL, proposer TEXT NOT NULL, proposer_email TEXT,
    created_at TEXT NOT NULL, decision TEXT, decided_at TEXT
);
CREATE TABLE IF NOT EXISTS journal (
    seq INTEGER PRIMARY KEY, at TEXT NOT NULL, action TEXT NOT NULL, object_id TEXT NOT NULL,
    actor_ref TEXT NOT NULL, previous TEXT NOT NULL, hash TEXT NOT NULL
);
"""


class ObservatoryError(RuntimeError):
    """A fixed error code safe to return to the caller."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status


def _utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="microseconds")


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _oldest_month(now: dt.datetime) -> str:
    return f"{now.year - 1:04d}-{now.month:02d}"


def _project_period(usage: list[dict], adoption: list[dict], threshold: int) -> tuple[list[dict], list[dict]]:
    """Release one exact projection per cohort to prevent subtraction across measures."""
    visible_adoption = sorted(
        (row for row in adoption if threshold <= row["active"] <= row["eligible"] - threshold),
        key=lambda row: (row["team"], row["tool"]),
    )
    adopted = {(row["team"], row["tool"]) for row in visible_adoption}
    visible_usage: dict[tuple[str, str], dict] = {}
    for row in sorted(usage, key=lambda row: (row["team"], row["tool"], USAGE_PRIORITY.index(row["kind"]))):
        cohort = (row["team"], row["tool"])
        if cohort not in adopted and row["people"] >= threshold:
            visible_usage.setdefault(cohort, row)
    return list(visible_usage.values()), visible_adoption


def _hash_journal(seq: int, at: str, action: str, object_id: str, actor_ref: str, previous: str) -> str:
    return hashlib.sha256(_encode([seq, at, action, object_id, actor_ref, previous]).encode()).hexdigest()


def _verify(db: sqlite3.Connection) -> dict:
    settings = db.execute("SELECT journal_seq, journal_hash FROM settings WHERE id = 1").fetchone()
    seq, previous = 0, ""
    for row in db.execute("SELECT seq, at, action, object_id, actor_ref, previous, hash FROM journal ORDER BY seq"):
        index, at, action, object_id, actor_ref, recorded_previous, recorded_hash = row
        seq += 1
        computed = _hash_journal(seq, at, action, object_id, actor_ref, previous)
        if index != seq or recorded_previous != previous or recorded_hash != computed:
            raise ObservatoryError(503, "journal_invalid")
        previous = computed
    if settings is None or seq != settings[0] or previous != settings[1]:
        raise ObservatoryError(503, "journal_invalid")
    return {"status": "verified", "events": seq, "head": previous}


class Observatory:
    """One organization per volume, with transactions covering state and its journal."""

    def __init__(self, state_dir: str, threshold: int, clock: Callable[[], dt.datetime] | None = None):
        directory = Path(state_dir) / "shadow-ai"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / "observatory.sqlite3"
        self.threshold = threshold
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        with contextlib.closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.executescript(_SCHEMA)
        with self._transaction(write=True, check_threshold=False) as db:
            db.execute("INSERT OR IGNORE INTO settings(id, privacy_threshold, actor_key) VALUES(1, ?, ?)",
                       (threshold, secrets.token_bytes(32)))
            settings = self._settings(db)
            _verify(db)
            if threshold < settings["privacy_threshold"]:
                raise ValueError("FACTORY_SHADOW_AI_PRIVACY_THRESHOLD cannot decrease for an existing Observatory")
            if threshold > settings["privacy_threshold"]:
                self._raise_threshold(db, threshold)
            self._prune(db, self._now())

    def _now(self) -> dt.datetime:
        return self.clock().astimezone(dt.timezone.utc)

    @staticmethod
    def _settings(db: sqlite3.Connection) -> sqlite3.Row:
        row = db.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        if row is None:
            raise ObservatoryError(503, "observatory_unavailable")
        return row

    @contextlib.contextmanager
    def _transaction(self, *, write: bool = False, check_threshold: bool = True) -> Iterator[sqlite3.Connection]:
        with contextlib.closing(sqlite3.connect(self.path, timeout=5, isolation_level=None)) as db:
            db.row_factory = sqlite3.Row
            if write:
                db.execute("PRAGMA secure_delete = ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            with db:
                if check_threshold and self._settings(db)["privacy_threshold"] != self.threshold:
                    # Old workers must not publish data after a threshold increase.
                    raise ObservatoryError(503, "observatory_unavailable")
                yield db

    def _raise_threshold(self, db: sqlite3.Connection, threshold: int) -> None:
        for row in db.execute("SELECT * FROM periods").fetchall():
            usage, adoption = _project_period(json.loads(row["team_usage"]), json.loads(row["adoption"]), threshold)
            db.execute("UPDATE periods SET team_usage = ?, adoption = ? WHERE period = ?", (
                _encode(usage), _encode(adoption), row["period"],
            ))
        db.execute("UPDATE evidence SET events = NULL, unique_users = NULL WHERE unique_users < ?", (threshold,))
        db.execute("UPDATE settings SET privacy_threshold = ? WHERE id = 1", (threshold,))
        self._journal(db, self._now(), "privacy_raised", "observatory", "system")

    @staticmethod
    def _prune(db: sqlite3.Connection, now: dt.datetime) -> None:
        db.execute("DELETE FROM periods WHERE period < ?", (_oldest_month(now),))
        db.execute("UPDATE evidence SET events = NULL, unique_users = NULL WHERE window_end < ? AND events IS NOT NULL",
                   (_utc(now - EVIDENCE_MAX_AGE),))
        db.execute("DELETE FROM proposals WHERE (decision IS NULL AND created_at < ?) OR decided_at < ?",
                   (_utc(now - PENDING_MAX_AGE), _utc(now - DECISIONS_MAX_AGE)))
        db.execute("""
            DELETE FROM proposals WHERE id IN (
                SELECT id FROM proposals WHERE decided_at IS NOT NULL
                ORDER BY decided_at DESC, id DESC LIMIT -1 OFFSET ?
            )
        """, (MAX_DECISIONS,))

    @staticmethod
    def _identity(db: sqlite3.Connection, principal: Principal) -> tuple[str, str | None]:
        if not principal.authenticated or not principal.user:
            raise ObservatoryError(403, "stable_identity_required")
        key = Observatory._settings(db)["actor_key"]
        actor = hmac.new(key, principal.user.casefold().encode(), "sha256").hexdigest()
        email = hmac.new(key, principal.email.casefold().encode(), "sha256").hexdigest() if principal.email else None
        return actor, email

    @staticmethod
    def _journal(db: sqlite3.Connection, now: dt.datetime, action: str, object_id: str, actor_ref: str) -> None:
        settings = Observatory._settings(db)
        seq, previous = settings["journal_seq"] + 1, settings["journal_hash"]
        tail = db.execute("SELECT seq, hash FROM journal ORDER BY seq DESC LIMIT 1").fetchone()
        if (tuple(tail) if tail else (0, "")) != (seq - 1, previous):
            raise ObservatoryError(503, "journal_invalid")
        at = _utc(now)
        digest = _hash_journal(seq, at, action, object_id, actor_ref, previous)
        db.execute("INSERT INTO journal VALUES(?, ?, ?, ?, ?, ?, ?)",
                   (seq, at, action, object_id, actor_ref, previous, digest))
        db.execute("UPDATE settings SET journal_seq = ?, journal_hash = ? WHERE id = 1", (seq, digest))

    def read(self, *, propose: bool, decide: bool) -> dict:
        now = self._now()
        with self._transaction() as db:
            evidence = db.execute("""
                SELECT window_start, window_end, events, unique_users FROM evidence
                WHERE unique_users >= ? AND window_end >= ?
            """, (self.threshold, _utc(now - EVIDENCE_MAX_AGE))).fetchone()
            periods = db.execute("SELECT * FROM periods WHERE period >= ? ORDER BY period DESC",
                                 (_oldest_month(now),)).fetchall()
            policies = db.execute("SELECT * FROM policies ORDER BY tool").fetchall()
            proposals = db.execute("""
                SELECT id, tool, status, alternative, expected_revision FROM proposals
                WHERE decision IS NULL AND created_at >= ? ORDER BY created_at, id
            """, (_utc(now - PENDING_MAX_AGE),)).fetchall() if propose or decide else []
        return {
            "privacy_threshold": self.threshold,
            "evidence": dict(evidence) if evidence else None,
            "team_usage": [
                {"period": row["period"], **item}
                for row in periods for item in json.loads(row["team_usage"])
            ],
            "adoption": [
                {"period": row["period"], **item}
                for row in periods for item in json.loads(row["adoption"])
            ],
            "policies": [dict(row) for row in policies],
            "proposals": [dict(row) for row in proposals],
            "capabilities": {"propose": propose, "decide": decide},
        }

    def import_evidence(self, start: dt.datetime, end: dt.datetime, events: int, unique_users: int,
                        principal: Principal) -> None:
        now = self._now()
        if not (dt.timedelta(days=1) <= end - start <= dt.timedelta(days=90)
                and now - EVIDENCE_MAX_AGE <= end <= now + dt.timedelta(minutes=5) and unique_users <= events):
            raise ObservatoryError(422, "invalid_request")
        with self._transaction(write=True) as db:
            actor, _ = self._identity(db, principal)
            self._prune(db, now)
            row = db.execute("SELECT window_start, window_end, events, unique_users FROM evidence").fetchone()
            visible = unique_users >= self.threshold
            incoming = (_utc(start), _utc(end), events if visible else None, unique_users if visible else None)
            if row and tuple(row) == incoming:
                return
            if row and incoming[1] <= row["window_end"]:
                raise ObservatoryError(409, "evidence_conflict")
            db.execute("INSERT OR REPLACE INTO evidence VALUES(1, ?, ?, ?, ?)", incoming)
            self._journal(db, now, "evidence_imported", incoming[1], actor)

    def import_period(self, period: str, usage: list[dict], adoption: list[dict], principal: Principal) -> None:
        now = self._now()
        if not _oldest_month(now) <= period < now.strftime("%Y-%m"):
            raise ObservatoryError(422, "invalid_request")
        # Reject duplicate measures before projection, including measures that won't be published.
        if (len({(row["team"], row["tool"], row["kind"]) for row in usage}) != len(usage)
                or len({(row["team"], row["tool"]) for row in adoption}) != len(adoption)
                or any(row["people"] > row["events"] for row in usage)
                or any(row["active"] > row["eligible"] for row in adoption)):
            raise ObservatoryError(422, "invalid_request")
        with self._transaction(write=True) as db:
            actor, _ = self._identity(db, principal)
            self._prune(db, now)
            existing = db.execute("SELECT team_usage, adoption FROM periods WHERE period = ?", (period,)).fetchone()
            visible_usage, visible_adoption = _project_period(usage, adoption, self.threshold)
            projected = (_encode(visible_usage), _encode(visible_adoption))
            if existing:
                if tuple(existing) != projected:
                    raise ObservatoryError(409, "period_conflict")
                return
            db.execute("INSERT INTO periods VALUES(?, ?, ?)", (period, *projected))
            self._journal(db, now, "period_imported", period, actor)

    @staticmethod
    def _check_policy(db: sqlite3.Connection, proposal: dict | sqlite3.Row) -> None:
        current = db.execute("SELECT revision FROM policies WHERE tool = ?", (proposal["tool"],)).fetchone()
        if (current["revision"] if current else 0) != proposal["expected_revision"]:
            raise ObservatoryError(409, "revision_conflict")
        if proposal["alternative"]:
            alternative = db.execute("SELECT status FROM policies WHERE tool = ?", (proposal["alternative"],)).fetchone()
            if proposal["alternative"] == proposal["tool"] or alternative is None or alternative["status"] != "approved":
                raise ObservatoryError(409, "alternative_unavailable")
            if db.execute("""
                WITH RECURSIVE chain(tool) AS (
                    VALUES(?) UNION
                    SELECT policies.alternative FROM policies JOIN chain USING(tool) WHERE alternative IS NOT NULL
                )
                SELECT 1 FROM chain WHERE tool = ?
            """, (proposal["alternative"], proposal["tool"])).fetchone():
                raise ObservatoryError(409, "alternative_cycle")
        if proposal["status"] != "approved" and db.execute(
            "SELECT 1 FROM policies WHERE alternative = ? LIMIT 1", (proposal["tool"],),
        ).fetchone():
            raise ObservatoryError(409, "alternative_in_use")
        if not current and db.execute("SELECT count(*) FROM policies").fetchone()[0] >= MAX_POLICIES:
            raise ObservatoryError(409, "policy_capacity")

    def propose(self, proposal: dict, principal: Principal) -> str:
        now = self._now()
        with self._transaction(write=True) as db:
            actor, email = self._identity(db, principal)
            self._prune(db, now)
            self._check_policy(db, proposal)
            existing = db.execute("""
                SELECT id FROM proposals WHERE tool = ? AND status = ? AND alternative IS ?
                AND expected_revision = ? AND proposer = ? AND decision IS NULL
            """, (proposal["tool"], proposal["status"], proposal["alternative"], proposal["expected_revision"],
                  actor)).fetchone()
            if existing:
                return existing["id"]
            if db.execute("SELECT count(*) FROM proposals WHERE decision IS NULL").fetchone()[0] >= MAX_PENDING_PROPOSALS:
                raise ObservatoryError(409, "proposal_capacity")
            proposal_id = uuid.uuid4().hex
            db.execute("""
                INSERT INTO proposals(id, tool, status, alternative, expected_revision, proposer, proposer_email, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """, (proposal_id, proposal["tool"], proposal["status"], proposal["alternative"],
                  proposal["expected_revision"], actor, email, _utc(now)))
            self._journal(db, now, "policy_proposed", f"{proposal_id}:{proposal['tool']}", actor)
            return proposal_id

    def decide(self, proposal_id: str, decision: str, principal: Principal) -> None:
        now = self._now()
        with self._transaction(write=True) as db:
            actor, email = self._identity(db, principal)
            proposal = db.execute("""
                SELECT * FROM proposals WHERE id = ? AND (
                    (decision IS NULL AND created_at >= ?) OR decided_at >= ?
                )
            """, (proposal_id, _utc(now - PENDING_MAX_AGE), _utc(now - DECISIONS_MAX_AGE))).fetchone()
            if proposal is None:
                raise ObservatoryError(404, "proposal_not_found")
            current_aliases = {actor, email} - {None}
            if current_aliases.intersection((proposal["proposer"], proposal["proposer_email"])):
                raise ObservatoryError(403, "separation_of_duties")
            if proposal["decision"] is not None:
                raise ObservatoryError(409, "proposal_decided")
            if decision == "approve":
                self._check_policy(db, proposal)
                db.execute("""
                    INSERT INTO policies VALUES(?, ?, ?, ?)
                    ON CONFLICT(tool) DO UPDATE SET status=excluded.status, alternative=excluded.alternative,
                                                    revision=excluded.revision
                """, (proposal["tool"], proposal["status"], proposal["alternative"], proposal["expected_revision"] + 1))
            db.execute("UPDATE proposals SET decision = ?, decided_at = ? WHERE id = ?",
                       (decision, _utc(now), proposal_id))
            self._journal(db, now, "policy_approved" if decision == "approve" else "policy_rejected",
                          f"{proposal_id}:{proposal['tool']}", actor)
            self._prune(db, now)


def main() -> int:
    """Verify a saved journal offline without printing identities or metrics."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("command", choices=["verify"])
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    try:
        uri = args.database.resolve().as_uri() + "?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True)) as db:
            db.execute("BEGIN")
            if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise ObservatoryError(503, "journal_invalid")
            print(_encode(_verify(db)))
    except (OSError, sqlite3.Error, ObservatoryError):
        print(_encode({"status": "unverifiable"}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
