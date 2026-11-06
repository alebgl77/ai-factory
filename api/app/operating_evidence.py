"""Bounded live operational observations and catalogue governance projection."""

from __future__ import annotations

import datetime as dt
import hmac
import json
import os
import re
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .engines.dataloop import evaluate_signals
from .engines.pipeline import evaluate_pipeline, evaluate_release_gate
from .shadow_ai import Observatory, ObservatoryError

if TYPE_CHECKING:
    from fastapi import FastAPI

MAX_OBSERVATIONS = 10_000
MAX_PER_CASE = 100
LOOKBACK = dt.timedelta(days=30)
RETENTION = dt.timedelta(days=90)
CLOCK_SKEW = dt.timedelta(minutes=5)
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)]
Version = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:+-]*$", max_length=64)]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)]
Timestamp = Annotated[str, StringConstraints(
    pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|\+00:00)$",
    max_length=32,
)]
Ratio = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Magnitude = Annotated[float, Field(ge=0, le=1_000_000, allow_inf_nan=False)]


class EvidenceError(ValueError):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status


def utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="microseconds")


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Observation(Input):
    observation_id: Identifier
    usecase: Identifier
    review_revision: Digest
    model_version: Version
    source: Identifier
    observed_at: Timestamp


class EvalMetrics(Input):
    faithfulness: Ratio | None = None
    answer_relevancy: Ratio | None = None
    context_precision: Ratio | None = None
    judge_score: Annotated[float, Field(ge=0, le=5, allow_inf_nan=False)] | None = None
    redteam_pass: bool | Ratio | None = None


class OnlineMetrics(Input):
    thumbs_down_ratio: Magnitude | None = None  # multiplier relative to pre-release baseline
    faithfulness_drop: Ratio | None = None
    error_rate: Ratio | None = None


class Receipt(Input):
    digest: Digest
    at: Timestamp


class Release(Observation):
    run_id: Identifier
    version: Version
    previous_version: Version | None = None
    artifact_digest: Digest
    ingested_at: Timestamp | None = None
    indexed_at: Timestamp | None = None
    versioned_at: Timestamp | None = None
    evaluated_at: Timestamp | None = None
    metrics: EvalMetrics | None = None
    deployment_receipt: Receipt | None = None
    online: OnlineMetrics | None = None
    rollback_receipt: Receipt | None = None

    @model_validator(mode="after")
    def timeline(self) -> Release:
        steps = (self.ingested_at, self.indexed_at, self.versioned_at, self.evaluated_at,
                 self.deployment_receipt.at if self.deployment_receipt else None)
        present = [dt.datetime.fromisoformat(at) for at in steps if at is not None]
        observed = dt.datetime.fromisoformat(self.observed_at)
        if (any(at is None for at in steps[:len(present)]) or present != sorted(present)
                or (present and present[-1] > observed)
                or (self.metrics is None) != (self.evaluated_at is None)
                or (self.online is not None and self.deployment_receipt is None)):
            raise ValueError("invalid_evidence_timeline")
        if self.rollback_receipt and (
            self.deployment_receipt is None or self.previous_version is None or self.previous_version == self.version
            or self.rollback_receipt.digest == self.deployment_receipt.digest
            or not present[-1] <= dt.datetime.fromisoformat(self.rollback_receipt.at) <= observed
        ):
            raise ValueError("invalid_rollback_receipt")
        return self


class Signal(Observation):
    signal: Literal["retrieval_hit_rate", "query_drift", "corpus_freshness", "eval_failure_cluster",
                    "cost_drift", "latency_drift"]
    value: Magnitude

    @model_validator(mode="after")
    def validate_observation(self) -> Signal:
        dt.datetime.fromisoformat(self.observed_at)
        if self.signal in {"retrieval_hit_rate", "corpus_freshness"} and self.value > 1:
            raise ValueError("invalid_signal_value")
        if self.signal == "eval_failure_cluster" and not self.value.is_integer():
            raise ValueError("invalid_signal_value")
        return self


def _normalize(observation: Observation) -> dict[str, Any]:
    body = observation.model_dump(exclude_none=True)
    for key, value in body.items():
        if key.endswith("_at"):
            body[key] = utc(dt.datetime.fromisoformat(value))
        elif key.endswith("_receipt"):
            body[key] = {**value, "at": utc(dt.datetime.fromisoformat(value["at"]))}
    return body


_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id = 1), publisher_key BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS evidence_v2 (
    kind TEXT NOT NULL, source TEXT NOT NULL, observation TEXT NOT NULL, usecase TEXT NOT NULL,
    revision TEXT NOT NULL, run TEXT NOT NULL, observed TEXT NOT NULL, payload TEXT NOT NULL,
    publisher_ref TEXT NOT NULL,
    PRIMARY KEY(kind, source, usecase, revision, observation)
);
CREATE INDEX IF NOT EXISTS evidence_v2_case ON evidence_v2 (usecase, revision, observed DESC);
CREATE INDEX IF NOT EXISTS evidence_v2_run ON evidence_v2 (source, usecase, revision, run, observed DESC)
    WHERE kind = 'release';
CREATE INDEX IF NOT EXISTS evidence_v2_expiry ON evidence_v2 (observed);
"""


class OperatingEvidence:
    def __init__(self, state_dir: str, sources: dict[str, frozenset[str]]) -> None:
        directory = Path(state_dir) / "operating-evidence"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / "observations.sqlite3"
        self.sources = sources
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'evidence'").fetchone():
                raise ValueError("unsupported development operating evidence schema")
            db.executescript(_SCHEMA)
            with db:
                db.execute("INSERT OR IGNORE INTO settings VALUES (1, ?)", (secrets.token_bytes(32),))

    @contextmanager
    def _transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=5, isolation_level=None)) as db:
            db.execute("PRAGMA trusted_schema = OFF")
            if not write:
                db.execute("PRAGMA query_only = ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            with db:
                yield db

    def record(self, observation: Observation, *, now: dt.datetime, publisher: str) -> bool:
        body = _normalize(observation)
        observed = dt.datetime.fromisoformat(body["observed_at"])
        if not now - LOOKBACK <= observed <= now + CLOCK_SKEW:
            raise EvidenceError(422, "invalid_observation_time")
        kind = "release" if isinstance(observation, Release) else "signal"
        run = observation.run_id if isinstance(observation, Release) else ""
        identity = (kind, observation.source, observation.usecase, observation.review_revision,
                    observation.observation_id)
        payload = canonical(body)
        with self._transaction(write=True) as db:
            db.execute("DELETE FROM evidence_v2 WHERE observed < ?", (utc(now - RETENTION),))
            existing = db.execute("""SELECT payload FROM evidence_v2 WHERE kind = ? AND source = ? AND usecase = ?
                                   AND revision = ? AND observation = ?""", identity).fetchone()
            if existing:
                if existing[0] != payload:
                    raise EvidenceError(409, "evidence_conflict")
                return False
            if run:
                previous = db.execute("""SELECT payload FROM evidence_v2 WHERE kind = 'release' AND source = ?
                                       AND usecase = ? AND revision = ? AND run = ? ORDER BY observed DESC LIMIT 1""",
                                      (observation.source, observation.usecase, observation.review_revision, run)).fetchone()
                if previous:
                    _verify_progress(json.loads(previous[0]), body)
            if db.execute("SELECT count(*) FROM evidence_v2").fetchone()[0] >= MAX_OBSERVATIONS:
                raise EvidenceError(409, "evidence_capacity")
            secret = db.execute("SELECT publisher_key FROM settings WHERE id = 1").fetchone()[0]
            publisher_ref = hmac.digest(secret, publisher.casefold().encode(), "sha256").hex()
            db.execute("""INSERT INTO evidence_v2 (kind, source, usecase, revision, observation,
                       run, observed, payload, publisher_ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                *identity, run, body["observed_at"], payload, publisher_ref,
            ))
        return True

    def read(self, registry: list[dict[str, Any]], *, now: dt.datetime) -> tuple[list[dict], list[dict]]:
        releases, signals = [], []
        oldest = utc(now - RETENTION)
        with self._transaction() as db:
            for item in registry:
                if not item.get("review_revision"):
                    continue
                where = (item["id"], item["review_revision"], oldest, MAX_PER_CASE)
                rows = db.execute("""SELECT payload FROM (
                    SELECT payload, observed, source, observation,
                           row_number() OVER (PARTITION BY source, run ORDER BY observed DESC) AS position
                    FROM evidence_v2 WHERE kind = 'release' AND usecase = ? AND revision = ? AND observed >= ?
                    ) WHERE position = 1 ORDER BY observed DESC, source, observation LIMIT ?""", where)
                releases.extend(json.loads(payload) for payload, in rows)
                rows = db.execute("""SELECT payload FROM evidence_v2 WHERE kind = 'signal' AND usecase = ?
                    AND revision = ? AND observed >= ? ORDER BY observed DESC, source, observation LIMIT ?""", where)
                signals.extend(json.loads(payload) for payload, in rows)
        return releases, signals


def _verify_progress(previous: dict, current: dict) -> None:
    mutable = {"observation_id", "observed_at", "online"}
    if (current["observed_at"] <= previous["observed_at"]
            or any(current.get(key) != value for key, value in previous.items() if key not in mutable)
            or ("online" in previous and "online" not in current)):
        raise EvidenceError(409, "evidence_run_conflict")


def project_catalogue(catalog: dict, observatory: Observatory | None) -> dict:
    if observatory is None:
        return catalog
    tools = catalog.get("tools") or []
    tool_ids = {tool["id"] for tool in tools}
    try:
        policies = {row["tool"]: row for row in observatory.catalogue_policies() if row["tool"] in tool_ids}
    except (ObservatoryError, OSError, sqlite3.Error):
        policies = None
    projected, denied = [], set()
    for tool in tools:
        key = tool["id"]
        policy = policies.get(key) if policies is not None else None
        copy = dict(tool)
        if policy:
            copy["shadow_policy"] = {"status": policy["status"], "revision": policy["revision"]}
            alternative = policy.get("alternative")
            if alternative in policies and policies[alternative]["status"] == "approved":
                copy["shadow_policy"]["alternative"] = alternative
        if policies is None or (policy and policy["status"] != "approved"):
            denied.add(key)
            copy["self_service"] = {**(tool.get("self_service") or {}), "allowed": False}
        projected.append(copy)
    agents = [
        {**agent, "self_service": {**(agent.get("self_service") or {}), "allowed": False}}
        if denied.intersection(agent.get("tools") or []) else agent for agent in catalog.get("agents") or []
    ]
    return {**catalog, "tools": projected, "agents": agents,
            "shadow_governance": "available" if policies is not None else "unavailable"}


def release_input(row: dict) -> dict:
    return {
        **row,
        "stages": {stage: {"at": row[field]} for stage, field in (
            ("ingestion", "ingested_at"), ("indexing", "indexed_at"), ("versioning", "versioned_at"),
        ) if field in row},
        "eval": {"at": row.get("evaluated_at"), "metrics": row.get("metrics") or {}},
        "online": {"at": row["observed_at"], **row["online"]} if row.get("online") else None,
    }


def validate_release(observation: Release, item: dict, factory_cfg: dict) -> None:
    if observation.deployment_receipt is not None and not evaluate_release_gate(
        release_input(_normalize(observation)), item, factory_cfg,
    ):
        raise EvidenceError(409, "release_not_approved")


def project_operations(snapshot: dict, store: OperatingEvidence | None, factory_cfg: dict, *, lang: str) -> dict:
    if store is None:
        return snapshot
    registry = snapshot["registry"]
    try:
        releases, signals = store.read(registry, now=dt.datetime.now(dt.timezone.utc))
        status = "available"
    except (OSError, sqlite3.Error, ValueError):
        releases, signals = [], []
        status = "unavailable"
    runs = [release_input(row) for row in releases]
    drift = [{**row, "at": row["observed_at"]} for row in signals]
    pipeline = evaluate_pipeline(runs, registry, factory_cfg, lang=lang, verified=True)
    dataloop = evaluate_signals(drift, registry, factory_cfg, lang=lang, verified=True)
    meta = snapshot["meta"]
    sections = {**meta["sections"], "pipeline": "live" if releases else "none",
                "dataloop": "live" if signals else "none"}
    return {**snapshot, "meta": {**meta, "sections": sections, "operating_evidence": status},
            "pipeline": pipeline, "dataloop": dataloop}


def configured_sources(value: str) -> dict[str, frozenset[str]]:
    if not value:
        return {}
    try:
        if len(value) > 65536:
            raise ValueError
        def unique(pairs: list[tuple[str, Any]]) -> dict:
            if len(pairs) != len(dict(pairs)):
                raise ValueError
            return dict(pairs)

        data = json.loads(value, object_pairs_hook=unique)
        if not isinstance(data, dict) or not 0 < len(data) <= 20:
            raise ValueError
        sources = {}
        for name, groups in data.items():
            if (not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64
                    or not isinstance(groups, list) or not 0 < len(groups) <= 30
                    or any(not isinstance(group, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", group)
                           for group in groups) or len(set(groups)) != len(groups)):
                raise ValueError
            sources[name] = frozenset(groups)
        return sources
    except (ValueError, RecursionError):
        raise ValueError("invalid FACTORY_OPERATING_EVIDENCE_SOURCES") from None


def configure_operating_evidence(app: FastAPI) -> None:
    from .routers.operating_evidence import router

    service = app.state.service
    service.catalogue_observatory = getattr(app.state, "shadow_ai", None)
    service.operating_evidence = None
    sources = configured_sources(os.environ.get("FACTORY_OPERATING_EVIDENCE_SOURCES", ""))
    if not sources:
        return
    if not service.guard.config.enforced or service.bu_policy is None or service.settings.demo_mode != "never":
        raise ValueError("operating evidence requires trusted proxy authentication, BU policy and live mode")
    for group in frozenset().union(*sources.values()):
        roles = {role for assigned, role in service.guard.config.role_map if group == assigned}
        if group not in service.bu_policy.groups or roles != {"viewer"}:
            raise ValueError("operating evidence publishers require a BU-scoped viewer group")
    service.operating_evidence = OperatingEvidence(service.settings.state_dir, sources)
    app.include_router(router)
