"""Governed decisions in a workspace bound to one store and one ERP target."""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from ._hashing import canonical, digest
from .adapters import OpportunityEvent, normalize_opportunity_event
from .algorithms import MatchResult, ModelRoute, RoutePolicy, match_customer, review_priority, select_route
from .connector import DraftConnector, validate_connector, write_and_verify
from .crm import CRM_CONFLICT, CRMBlockedOpportunity, CRMError, validate_source
from .identity import ASSESSMENT, READ, Actor, WorkActor
from .store import Store, WorkbenchError

WORKSPACE = "demo"
_SIMULATED_CRM_SOURCE = "crm-sim"
_WORKSPACE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_SUBJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PROFILE_KEYS = frozenset({"id", "version", "fingerprint"})
_PROVENANCE_KEYS = frozenset({"issuer", "channel", "identity", "request_id", "proof_sha256", "expires_at"})
_CASE_VISIBLE = (
    "(? OR (source <> 'sdk-review' AND json_type(record, '$.profile') IS NULL "
    "AND json_type(record, '$.private') IS NULL))"
)
APPROVAL_LIFETIME = dt.timedelta(days=7)
DEMO_EVENT_ID = "demo-event-042"
DEMO_SUBJECT = "opp-demo-042"
SUBMIT_ROLES = frozenset({"editor", "maintainer", "admin"})
QUEUE_STATES = frozenset({"waiting_review", "applying"})
AGENTS = frozenset({"operations", "finance", "governance"})
DECISIONS = frozenset({"approve", "reject", "request_changes"})
FEEDBACK = frozenset({"helpful", "needs_work"})
STRATEGIC = re.compile(r"\b(stratégi\w*|strateg\w*|budget\w*|policy|politique|réorganis\w*|invest\w*)\b", re.I)
ACTION_REQUEST = re.compile(r"\b(approve|approv\w*|authoriz\w*|autoris\w*|cancel|annul\w*|"
                            r"execute|exécut\w*|modify|modifi\w*|change|chang\w*|deploy|déploi\w*)\b", re.I)
INFORMATIONAL = re.compile(
    r"\b(status|statut|état|preuve\w*|evidence|amount|montant|client|customer|priority|priorité|"
    r"condition\w*|explain|expliqu\w*|blocage|blocked|summary|résum\w*)\b", re.I
)
CUSTOMERS = (
    {"id": "erp-acme", "tenant": WORKSPACE, "name": "Atelier Acme", "reference": "CRM-ACME"},
    {"id": "erp-nova", "tenant": WORKSPACE, "name": "Nova Services", "reference": "CRM-NOVA"},
)
PUBLIC_CASE_FIELDS = (
    "id", "title", "kind", "status", "revision", "proposal_hash", "reason", "action", "evidence",
    "receipt", "created_at", "expires_at", "decision",
)


def _check_utf8(*values: str) -> None:
    try:
        for value in values:
            value.encode("utf-8")
    except UnicodeEncodeError:
        raise WorkbenchError("invalid_utf8", 422) from None


def _bounded_text(value: object, limit: int) -> bool:
    return isinstance(value, str) and len(value) <= limit and bool(value.strip())


def _profile_snapshot(profile: dict[str, str] | None) -> dict[str, str] | None:
    if profile is None:
        return None
    if (not isinstance(profile, dict) or profile.keys() != _PROFILE_KEYS
            or not isinstance(profile["id"], str) or not _WORKSPACE_ID.fullmatch(profile["id"])
            or not _bounded_text(profile["version"], 32) or not profile["version"].isprintable()
            or not isinstance(profile["fingerprint"], str) or not _SHA256.fullmatch(profile["fingerprint"])):
        raise WorkbenchError("invalid_review_profile", 422)
    return profile.copy()


def _approval_proof(provenance: dict[str, str] | None) -> dict[str, str] | None:
    if provenance is None:
        return None
    if (not isinstance(provenance, dict) or provenance.keys() != _PROVENANCE_KEYS
            or any(not _bounded_text(value, 256) or value != value.strip() or not value.isprintable()
                   for value in provenance.values())
            or provenance["channel"] not in {"slack", "teams", "service"}
            or not _SHA256.fullmatch(provenance["proof_sha256"])):
        raise WorkbenchError("invalid_approval_proof", 422)
    try:
        expires = dt.datetime.fromisoformat(provenance["expires_at"])
        if expires.utcoffset() != dt.timedelta():
            raise ValueError
    except (ValueError, OverflowError):
        raise WorkbenchError("invalid_approval_proof", 422) from None
    _check_utf8(*provenance.values())
    return provenance.copy()


def local_advisor(base_url: str, key: str, transport: httpx.BaseTransport | None = None) -> Callable:
    """Fixed local gateway alias. Model text has no ability to invoke actions."""
    instructions = (
        "Explain the supplied synthetic business case and distinguish facts from assumptions. "
        "Answer in the employee's language. Your answer is advice only. Never claim you can approve "
        "or execute an action. If the evidence is insufficient, say what a human should review."
    )

    def advise(agent: str, message: str, record: dict[str, Any]) -> str:
        context = json.dumps(
            {"specialty": agent, **{k: record[k] for k in ("title", "status", "kind", "reason", "action")}},
            ensure_ascii=False,
        )
        payload = {
            "model": "chat-sensitive",
            "messages": [{"role": "system", "content": instructions},
                         {"role": "user", "content": context + "\nQuestion: " + message}],
            "temperature": 0,
            "max_tokens": 512,
            "stream": False,
            "metadata": {"sensitivity": "C3", "mask_input": True, "mask_output": True,
                         "turn_off_message_logging": True},
        }
        try:
            with httpx.Client(base_url=base_url, timeout=httpx.Timeout(12, connect=2), transport=transport,
                              trust_env=False, follow_redirects=False) as client:
                with client.stream("POST", "/v1/chat/completions", json=payload,
                                   headers={"Authorization": f"Bearer {key}"}) as response:
                    response.raise_for_status()
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        if len(content) + len(chunk) > 32 * 1024:
                            raise ValueError("response limit")
                        content.extend(chunk)
            answer = json.loads(content)["choices"][0]["message"]["content"]
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("empty model answer")
            return answer.strip()[:4000]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            raise WorkbenchError("local_agent_unavailable", 503) from None

    return advise


class Workbench:
    def __init__(self, path: Path, mode: str = "demo", clock: Callable[[], dt.datetime] | None = None,
                 advisor: Callable[[str, str, dict[str, Any]], str] | None = None, *,
                 workspace: str = WORKSPACE, connector: DraftConnector | None = None,
                 customers: Sequence[Mapping[str, str]] | None = None, crm_source: str = _SIMULATED_CRM_SOURCE,
                 crm_sources: Sequence[str] = (),
                 defer_execution: bool = False) -> None:
        if not isinstance(workspace, str) or not _WORKSPACE_ID.fullmatch(workspace):
            raise ValueError("invalid workspace")
        if mode not in {"demo", "protected_demo"}:
            raise ValueError("invalid workbench mode")
        if not isinstance(crm_source, str) or not _SUBJECT.fullmatch(crm_source):
            raise ValueError("invalid CRM source")
        try:
            if isinstance(crm_sources, (str, bytes)):
                raise ValueError
            trusted_sources = frozenset(validate_source(source) for source in crm_sources)
            if crm_source in trusted_sources:
                raise ValueError
        except (CRMError, TypeError, ValueError):
            raise ValueError("invalid trusted CRM sources") from None
        self._workspace = workspace
        self._connector = connector
        self._target = validate_connector(connector) if connector is not None else "sandbox-erp"
        catalogue = CUSTOMERS if customers is None and workspace == WORKSPACE and connector is None else customers
        self._customers = tuple(dict(row) for row in catalogue or ())
        self._customer_ids = frozenset(row["id"] for row in self._customers if isinstance(row.get("id"), str))
        references: dict[str, list[dict[str, str]]] = {}
        for row in self._customers:
            reference = row.get("reference")
            if (isinstance(reference, str) and reference and row.get("tenant") == workspace
                    and isinstance(row.get("id"), str)):
                references.setdefault(reference, []).append(row)
        self._customer_matches = {
            reference: match_customer({"customer_ref": reference}, rows, tenant=workspace,
                                      allowed_ids={row["id"] for row in rows})
            for reference, rows in references.items()
        }
        self._crm_source = crm_source
        self._crm_sources = trusted_sources
        self._defer_execution = defer_execution
        self.store = Store(path, workspace, self._target)
        self.mode = mode
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self.advisor = advisor
        self._seeded = False
        self._seed_lock = threading.Lock()

    @property
    def workspace(self) -> str:
        return self._workspace

    def _time(self) -> str:
        return self.clock().astimezone(dt.timezone.utc).isoformat()

    def _requires_authentication(self) -> bool:
        return (self.mode == "protected_demo" or self.workspace != WORKSPACE or self._connector is not None
                or bool(self._crm_sources))

    def check_access(self, actor: WorkActor) -> None:
        if not actor.authenticated and self._requires_authentication():
            raise WorkbenchError("authentication_required", 401)
        if not actor.role_for(READ):
            raise WorkbenchError("forbidden", 403)

    def capabilities(self, actor: WorkActor) -> dict[str, bool | str]:
        self.check_access(actor)
        return {"can_submit": bool(actor.roles & SUBMIT_ROLES),
                "can_decide": bool(actor.role_for(ASSESSMENT)),
                "chat_source": "local" if self.advisor is not None else "simulation"}

    @staticmethod
    def _owns(record: dict[str, Any], actor: WorkActor) -> bool:
        return actor.authenticated and bool(actor.ids & set(record["proposer_ids"]))

    @staticmethod
    def _priority(record: dict[str, Any], now: dt.datetime) -> int:
        age = (now - dt.datetime.fromisoformat(record["created_at"])).total_seconds() / 3600
        amount = record["action"]["fields"]["amount_minor"] if record["action"] else 0
        return review_priority(record["kind"], max(0.0, age), amount)

    def _allows_draft_source(self, source: str) -> bool:
        return self._connector is None or source != _SIMULATED_CRM_SOURCE

    def _public(self, record: dict[str, Any], actor: WorkActor, now: dt.datetime | None = None) -> dict[str, Any]:
        now = now or self.clock()
        result = {key: record[key] for key in PUBLIC_CASE_FIELDS}
        if "profile" in record:
            result["profile"] = record["profile"]
        if record.get("crm_reconciliation_required"):
            result["reconciliation_required"] = True
        result["priority"] = self._priority(record, now)
        result["can_decide"] = (record["status"] in QUEUE_STATES and bool(actor.role_for(ASSESSMENT))
                                and not record.get("crm_reconciliation_required")
                                and not record.get("crm_unconfirmed")
                                and (record["action"] is None or self._allows_draft_source(record["source"]))
                                and not self._owns(record, actor)
                                and (record["status"] != "applying" or record.get("decision_actor") == actor.actor))
        if record["status"] == "applying" and result["can_decide"]:
            result["decision_rationale"] = record.get("decision_rationale")
        result["approval_expired"] = self._expired(record, now)
        return result

    @staticmethod
    def _expired(record: dict[str, Any], now: dt.datetime) -> bool:
        return (record["status"] == "waiting_review" and bool(record["expires_at"])
                and now >= dt.datetime.fromisoformat(record["expires_at"]))

    def cases(self, actor: WorkActor, *, limit: int = 200) -> list[dict[str, Any]]:
        self.check_access(actor)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        now = self.clock()
        with self.store.transaction(write=False) as connection:
            connection.create_function("review_priority", 1, lambda raw: self._priority(json.loads(raw), now),
                                       deterministic=True)
            rows = connection.execute(
                f"""SELECT record FROM cases WHERE {_CASE_VISIBLE}
                    ORDER BY CASE WHEN status IN ('waiting_review','applying') THEN 0 ELSE 1 END,
                             review_priority(record) DESC, json_extract(record,'$.created_at'), id
                    LIMIT ?""",
                (actor.authenticated, limit),
            ).fetchall()
        return [self._public(json.loads(row["record"]), actor, now) for row in rows]

    @staticmethod
    def _load_visible_case(connection: sqlite3.Connection, case_id: str, actor: WorkActor) -> dict[str, Any]:
        row = connection.execute(
            f"SELECT record FROM cases WHERE id = ? AND {_CASE_VISIBLE}", (case_id, actor.authenticated),
        ).fetchone()
        if row is None:
            raise WorkbenchError("case_not_found", 404)
        return json.loads(row["record"])

    @staticmethod
    def _load_visible_subject(connection: sqlite3.Connection, source: str, subject: str,
                              actor: WorkActor) -> dict[str, Any] | None:
        row = connection.execute(
            f"SELECT CASE WHEN {_CASE_VISIBLE} THEN record END AS record FROM cases WHERE source = ? AND subject = ?",
            (actor.authenticated, source, subject),
        ).fetchone()
        if row is None:
            return None
        if row["record"] is None:
            raise WorkbenchError("case_not_found", 404)
        return json.loads(row["record"])

    def get_case(self, case_id: str, actor: WorkActor) -> dict[str, Any]:
        self.check_access(actor)
        with self.store.transaction(write=False) as connection:
            record = self._load_visible_case(connection, case_id, actor)
        return self._public(record, actor)

    def _new_case(self, *, case_id: str, source: str, subject: str, title: str, kind: str,
                  reason: str, evidence: list[str], actor: WorkActor, action: dict | None = None,
                  revision: int = 1, record_version: int = 0, source_payload_hash: str = "",
                  profile: dict[str, str] | None = None, private: bool = False) -> dict:
        private = private or self._requires_authentication()
        now = self.clock().astimezone(dt.timezone.utc)
        expires_at = (now + APPROVAL_LIFETIME).isoformat() if action else None
        proposal = {"workspace": self.workspace, "case": case_id, "revision": revision,
                    "source": source, "subject": subject, "record_version": record_version,
                    "kind": kind, "title": title, "action": action, "reason": reason,
                    "policy_version": 1, "expires_at": expires_at, "evidence": evidence}
        if profile is not None:
            proposal["profile"] = profile
        if private:
            proposal["private"] = True
        record = {"id": case_id, "source": source, "subject": subject, "title": title, "kind": kind,
                  "status": "waiting_review", "revision": revision, "record_version": record_version,
                  "proposal_hash": digest(proposal), "source_payload_hash": source_payload_hash,
                  "proposer_ids": sorted(actor.ids), "reason": reason, "action": action, "evidence": evidence,
                  "receipt": None, "decision": None, "created_at": now.isoformat(), "expires_at": expires_at}
        if profile is not None:
            record["profile"] = profile
        if private:
            record["private"] = True
        return record

    def propose_review(self, actor: WorkActor, *, subject: str, title: str, kind: str, reason: str,
                       evidence: list[str], profile: dict[str, str] | None = None) -> dict[str, Any]:
        """Open or revise a bounded, non-executable review in the current workspace."""
        self.check_access(actor)
        if not actor.authenticated or not actor.ids:
            raise WorkbenchError("authentication_required", 401)
        if not actor.roles & SUBMIT_ROLES:
            raise WorkbenchError("forbidden", 403)
        if (not isinstance(subject, str) or not _SUBJECT.fullmatch(subject)
                or kind not in ("strategic", "undocumented")
                or not _bounded_text(title, 160) or not _bounded_text(reason, 1000)
                or not isinstance(evidence, list) or len(evidence) > 12
                or any(not _bounded_text(item, 320) for item in evidence)):
            raise WorkbenchError("invalid_review", 422)
        _check_utf8(title, reason, *evidence)
        profile = _profile_snapshot(profile)
        title, reason = title.strip(), reason.strip()
        evidence = [item.strip() for item in evidence]
        fingerprint = digest([title, kind, reason, evidence, profile])
        source = "sdk-review"
        with self.store.transaction() as connection:
            row = connection.execute("SELECT record FROM cases WHERE source = ? AND subject = ?",
                                     (source, subject)).fetchone()
            previous = json.loads(row["record"]) if row else None
            if previous:
                if not self._owns(previous, actor):
                    raise WorkbenchError("review_subject_conflict", 409)
                if previous["source_payload_hash"] == fingerprint:
                    return self._public(previous, actor)
                if previous["status"] not in {"waiting_review", "changes_requested"}:
                    raise WorkbenchError("already_decided", 409)
            record = self._new_case(
                case_id=previous["id"] if previous else uuid.uuid4().hex,
                source=source, subject=subject, title=title, kind=kind, reason=reason,
                evidence=evidence, actor=actor, revision=previous["revision"] + 1 if previous else 1,
                source_payload_hash=fingerprint, profile=profile,
            )
            self.store.put_case(connection, record)
        return self._public(record, actor)

    def submit(self, payload: dict[str, Any], actor: WorkActor, *,
               profile: dict[str, str] | None = None) -> dict[str, Any]:
        return self._submit(payload, actor, seed=False, profile=profile)

    def _opportunity_case(self, event: OpportunityEvent, actor: WorkActor, previous: dict | None, *,
                          profile: dict[str, str] | None, exact_reference: bool = False) -> dict:
        if event.customer_ref is not None or exact_reference:
            match = self._customer_matches.get(event.customer_ref, MatchResult("missing"))
        else:
            match = match_customer(
                {"customer_name": event.customer_name}, self._customers,
                tenant=self.workspace, allowed_ids=self._customer_ids,
            )
        fields = {"customer_id": match.candidate_id, "crm_opportunity": event.subject,
                  "amount_minor": event.amount_minor, "currency": event.currency}
        action = {"type": "erp.create_draft_order", "fields": fields} if match.status == "matched" else None
        reason = ("Customer matched. Draft ERP order requires approval." if action
                  else "No unambiguous customer match. Human review is required.")
        evidence = [f"CRM: {event.source} / {event.subject} / v{event.record_version}",
                    f"Customer match: {match.status} ({match.score:.2f})",
                    f"Amount: {event.amount_minor} minor units {event.currency}"]
        return self._new_case(
            case_id=previous["id"] if previous else uuid.uuid4().hex, source=event.source,
            subject=event.subject, title=event.title, kind="action" if action else "undocumented",
            reason=reason, evidence=evidence, actor=actor, action=action,
            revision=previous["revision"] + 1 if previous else 1, record_version=event.record_version,
            source_payload_hash=event.record_fingerprint,
            profile=previous.get("profile") if profile is None and previous else profile,
            private=previous is not None and "private" in previous,
        )

    @staticmethod
    def _execution_claimed(record: dict[str, Any]) -> bool:
        return (record["status"] == "executed"
                or (record["status"] == "applying" and record.get("execution_started") is not False))

    def _submit(self, payload: dict[str, Any], actor: WorkActor, *, seed: bool,
                profile: dict[str, str] | None = None) -> dict[str, Any]:
        self.check_access(actor)
        if not actor.roles & SUBMIT_ROLES:
            raise WorkbenchError("forbidden", 403)
        try:
            event = normalize_opportunity_event(payload, tenant=self.workspace)
        except (ValueError, TypeError):
            raise WorkbenchError("invalid_event", 422) from None
        if event.source != self._crm_source:
            raise WorkbenchError("invalid_event", 422)
        if not self._allows_draft_source(event.source):
            raise WorkbenchError("simulated_crm_requires_sandbox", 409)
        if self.workspace == WORKSPACE and not seed and (event.id == DEMO_EVENT_ID or event.subject == DEMO_SUBJECT):
            raise WorkbenchError("reserved_demo_identity", 422)
        profile = _profile_snapshot(profile)
        if profile is not None and (not actor.authenticated or not actor.ids):
            raise WorkbenchError("authentication_required", 401)
        source_payload_hash = event.record_fingerprint
        with self.store.transaction() as connection:
            previous_event = connection.execute(
                "SELECT fingerprint, case_id FROM events WHERE source = ? AND id = ?", (event.source, event.id)
            ).fetchone()
            if previous_event:
                record = self._load_visible_case(connection, previous_event["case_id"], actor)
                if previous_event["fingerprint"] != event.fingerprint:
                    raise WorkbenchError("event_conflict", 409)
                return self._public(record, actor)
            previous = self._load_visible_subject(connection, event.source, event.subject, actor)
            if previous and event.record_version < previous["record_version"]:
                raise WorkbenchError("stale_event", 409)
            if previous and event.record_version == previous["record_version"]:
                if previous["source_payload_hash"] != source_payload_hash:
                    raise WorkbenchError("event_revision_conflict", 409)
                record = previous
            else:
                if previous and self._execution_claimed(previous):
                    raise WorkbenchError("reconciliation_required", 409)
                record = self._opportunity_case(event, actor, previous, profile=profile)
                self.store.put_case(connection, record)
            connection.execute("INSERT INTO events(source,id,fingerprint,case_id) VALUES (?,?,?,?)",
                               (event.source, event.id, event.fingerprint, record["id"]))
        return self._public(record, actor)

    def _check_crm_ingestion(self, source: str, actor: WorkActor, *,
                             profile: dict[str, str] | None) -> dict[str, str] | None:
        self.check_access(actor)
        if not actor.authenticated or not actor.ids:
            raise WorkbenchError("authentication_required", 401)
        if source not in self._crm_sources or not actor.roles & SUBMIT_ROLES:
            raise WorkbenchError("crm_source_not_authorized", 403)
        return _profile_snapshot(profile)

    def _apply_crm_observation(self, source: str, subject: str, version: int,
                               event: OpportunityEvent | CRMBlockedOpportunity | None,
                               actor: WorkActor, *, profile: dict[str, str] | None,
                               incomplete: bool = False) -> tuple[str, ...]:
        """Trusted provider observations, including those that withdraw an opportunity."""
        profile = self._check_crm_ingestion(source, actor, profile=profile)
        if isinstance(event, OpportunityEvent):
            data = event.to_dict()["data"]
            data.pop("record_version")
        else:
            data = {"blocked": event.code} if event is not None else None
        fingerprint = digest(data)
        conflict_fingerprint = digest({"blocked": CRM_CONFLICT.code})
        with self.store.transaction() as connection:
            head = connection.execute(
                "SELECT record_version,fingerprint FROM crm_revisions WHERE source = ? AND subject = ?",
                (source, subject),
            ).fetchone()
            previous = self._load_visible_subject(connection, source, subject, actor)
            if incomplete and isinstance(event, OpportunityEvent) and previous is None:
                return ("ignored",)
            if head is not None:
                if version < head["record_version"]:
                    return ("stale",)
                if version == head["record_version"]:
                    held = bool(previous and previous.get("crm_reconciliation_required"))
                    if head["fingerprint"] == conflict_fingerprint:
                        return ("conflicts", "reconciliation_required") if held else ("conflicts",)
                    if fingerprint != head["fingerprint"]:
                        event, fingerprint = CRM_CONFLICT, conflict_fingerprint
                    elif not (not incomplete and isinstance(event, OpportunityEvent)
                              and previous and previous.get("crm_unconfirmed")):
                        return ("reconciliation_required",) if held else ("replayed",)
            elif previous is not None and version <= previous["record_version"]:
                if version < previous["record_version"]:
                    return ("stale",)
                if isinstance(event, OpportunityEvent) and event.record_fingerprint == previous["source_payload_hash"]:
                    return ("replayed",)
                event, fingerprint = CRM_CONFLICT, conflict_fingerprint

            unconfirmed = incomplete and isinstance(event, OpportunityEvent)
            if (unconfirmed and previous and not previous.get("crm_unconfirmed")
                    and (previous["action"] is None or (head and fingerprint == head["fingerprint"]))):
                return ("ignored",)
            outcomes = []
            if isinstance(event, CRMBlockedOpportunity):
                outcomes.append("conflicts" if event == CRM_CONFLICT else "blocked")
            elif unconfirmed:
                outcomes.append("blocked")
            if previous is not None and self._execution_claimed(previous):
                if head is None or fingerprint != head["fingerprint"] or previous.get("crm_reconciliation_required"):
                    previous["crm_reconciliation_required"] = True
                    self.store.put_case(connection, previous)
                    outcomes.append("reconciliation_required")
            elif isinstance(event, CRMBlockedOpportunity) or unconfirmed:
                if unconfirmed:
                    reason = "The CRM sync was incomplete. Finish the sync before approving this opportunity."
                elif event == CRM_CONFLICT:
                    reason = "The CRM reported conflicting data at the same revision. Sync a newer consistent revision."
                else:
                    reason = "The CRM opportunity is won but its business data cannot prepare a draft. Correct the source and sync again."
                record = self._new_case(
                    case_id=previous["id"] if previous else uuid.uuid4().hex,
                    source=source, subject=subject, title="CRM opportunity requires review", kind="undocumented",
                    reason=reason, evidence=[f"CRM: {source} / {subject} / v{version}"], actor=actor,
                    revision=previous["revision"] + 1 if previous else 1, record_version=version,
                    source_payload_hash=fingerprint, profile=previous.get("profile") if previous else profile, private=True,
                )
                if unconfirmed:
                    record["crm_unconfirmed"] = True
                self.store.put_case(connection, record)
            elif isinstance(event, OpportunityEvent):
                record = self._opportunity_case(event, actor, previous, profile=profile, exact_reference=True)
                self.store.put_case(connection, record)
                connection.execute("INSERT INTO events(source,id,fingerprint,case_id) VALUES (?,?,?,?)",
                                   (source, event.id, event.fingerprint, record["id"]))
                outcomes.append("applied")
            elif previous is not None and previous["status"] != "withdrawn":
                record = self._new_case(
                    case_id=previous["id"], source=source, subject=subject, title=previous["title"],
                    kind=previous["kind"], reason="The CRM opportunity is no longer won. The proposal was withdrawn.",
                    evidence=[f"CRM: {source} / {subject} / v{version}"], actor=actor,
                    revision=previous["revision"] + 1, record_version=version,
                    source_payload_hash=fingerprint, profile=previous.get("profile"), private=True,
                )
                record["status"] = "withdrawn"
                self.store.put_case(connection, record)
                outcomes.append("withdrawn")
            connection.execute(
                """INSERT INTO crm_revisions(source,subject,record_version,fingerprint) VALUES (?,?,?,?)
                   ON CONFLICT(source,subject) DO UPDATE SET record_version=excluded.record_version,
                   fingerprint=excluded.fingerprint""",
                (source, subject, version, fingerprint),
            )
        return tuple(outcomes) if outcomes else ("ignored",)

    def decide(self, case_id: str, actor: WorkActor, *, decision: str, revision: int,
               proposal_hash: str, rationale: str, provenance: dict[str, str] | None = None) -> dict[str, Any]:
        """Commit the exact decision. The embedding host verifies any external provenance."""
        self.check_access(actor)
        if not actor.role_for(ASSESSMENT):
            raise WorkbenchError("forbidden", 403)
        if (not isinstance(decision, str) or decision not in DECISIONS or not _bounded_text(rationale, 1000)
                or type(revision) is not int or revision < 1
                or not isinstance(proposal_hash, str) or not _SHA256.fullmatch(proposal_hash)):
            raise WorkbenchError("invalid_decision", 422)
        _check_utf8(rationale)
        proof = _approval_proof(provenance)
        with self.store.transaction() as connection:
            record = self._load_visible_case(connection, case_id, actor)
            if revision != record["revision"] or proposal_hash != record["proposal_hash"]:
                raise WorkbenchError("stale_proposal", 409)
            if record.get("crm_reconciliation_required"):
                raise WorkbenchError("reconciliation_required", 409)
            if record.get("crm_unconfirmed"):
                raise WorkbenchError("crm_sync_incomplete", 409)
            if (decision == "approve" and record["status"] in QUEUE_STATES and record["action"] is not None
                    and not self._allows_draft_source(record["source"])):
                raise WorkbenchError("simulated_crm_requires_sandbox", 409)
            if decision == "approve" and self._owns(record, actor):
                raise WorkbenchError("self_approval_forbidden", 403)
            if proof is not None:
                expires = dt.datetime.fromisoformat(proof["expires_at"])
                if self.clock() >= expires:
                    raise WorkbenchError("approval_proof_expired", 409)
                existing = connection.execute(
                    "SELECT case_id,revision,metadata FROM approval_provenance WHERE issuer = ? AND request_id = ?",
                    (proof["issuer"], proof["request_id"]),
                ).fetchone()
                if existing and (existing["case_id"], existing["revision"], existing["metadata"]) != (
                    case_id, revision, canonical(proof).decode()
                ):
                    raise WorkbenchError("approval_replay", 409)
                if not existing and record["status"] != "waiting_review":
                    raise WorkbenchError("already_decided", 409)
            if record["status"] != "waiting_review":
                prior = connection.execute(
                    "SELECT actor, decision, rationale FROM decisions WHERE case_id = ? AND revision = ?",
                    (case_id, revision),
                ).fetchone()
                if not prior or (prior["actor"], prior["decision"], prior["rationale"]) != (
                    actor.actor, decision, rationale.strip()
                ):
                    raise WorkbenchError("already_decided", 409)
                if record["status"] != "applying":
                    return self._public(record, actor)
            else:
                if decision == "approve" and self._expired(record, self.clock()):
                    raise WorkbenchError("proposal_expired", 409)
                status = {"reject": "rejected", "request_changes": "changes_requested",
                          "approve": "applying" if record["action"] else "decided"}[decision]
                record.update(status=status, decision=decision, decision_actor=actor.actor,
                              decision_rationale=rationale.strip())
                if status == "applying":
                    record["execution_started"] = False
                connection.execute(
                    """INSERT INTO decisions(case_id,revision,actor,proposal_hash,decision,rationale,created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (case_id, revision, actor.actor, proposal_hash, decision, rationale.strip(), self._time()),
                )
                if proof is not None:
                    connection.execute(
                        "INSERT INTO approval_provenance(issuer,request_id,case_id,revision,metadata) VALUES (?,?,?,?,?)",
                        (proof["issuer"], proof["request_id"], case_id, revision, canonical(proof).decode()),
                    )
                self.store.put_case(connection, record)
        if record["status"] == "applying" and not self._defer_execution:
            record = self._apply(record)
        return self._public(record, actor)

    def pending_execution_ids(self, limit: int = 100) -> list[str]:
        """Bounded queue for a trusted host worker after a restart."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self.store.transaction(write=False) as connection:
            return [row["id"] for row in connection.execute(
                """SELECT id FROM cases WHERE status = 'applying'
                   AND json_type(record,'$.crm_reconciliation_required') IS NULL AND (? OR source <> ?)
                   ORDER BY id LIMIT ?""", (self._connector is None, _SIMULATED_CRM_SOURCE, limit),
            )]

    def reconcile_pending(self, case_id: str) -> dict[str, Any]:
        """Trusted host recovery. Resume only a stored, exact, previously approved draft."""
        with self.store.transaction(write=False) as connection:
            record = self.store.load_case(connection, case_id)
            if record.get("crm_reconciliation_required"):
                raise WorkbenchError("reconciliation_required", 409)
            prior = connection.execute(
                "SELECT decision,proposal_hash FROM decisions WHERE case_id = ? AND revision = ?",
                (case_id, record["revision"]),
            ).fetchone()
            if (record["status"] not in {"applying", "executed"} or record["action"] is None
                    or prior is None or (prior["decision"], prior["proposal_hash"]) != (
                        "approve", record["proposal_hash"]
                    )):
                raise WorkbenchError("no_pending_execution", 409)
        if record["status"] == "applying":
            record = self._apply(record)
        return {"case_id": case_id, "status": record["status"], "receipt": record["receipt"]}

    def _read_exact_execution(self, connection: sqlite3.Connection, record: dict[str, Any]) -> dict[str, Any]:
        current = self.store.load_case(connection, record["id"])
        if (current["revision"], current["proposal_hash"]) != (record["revision"], record["proposal_hash"]):
            raise WorkbenchError("stale_proposal", 409)
        return current

    def _write_candidate(self, connection: sqlite3.Connection, record: dict[str, Any]) -> dict[str, Any]:
        current = self._read_exact_execution(connection, record)
        if current.get("crm_reconciliation_required"):
            raise WorkbenchError("reconciliation_required", 409)
        if current["status"] == "executed":
            return current
        if current["status"] != "applying":
            raise WorkbenchError("no_pending_execution", 409)
        if not self._allows_draft_source(current["source"]):
            raise WorkbenchError("simulated_crm_requires_sandbox", 409)
        return current

    def _apply(self, record: dict[str, Any]) -> dict[str, Any]:
        # Recheck and claim under the same lock that accepts newer CRM revisions.
        # After this point even a lost response requires reconciliation.
        with self.store.transaction() as connection:
            current = self._write_candidate(connection, record)
            if current["status"] == "executed":
                return current
            if current.get("execution_started") is not True:
                current["execution_started"] = True
                self.store.put_case(connection, current)
            authoritative = self._connector is None and connection.execute(
                "SELECT 1 FROM crm_revisions WHERE source = ? AND subject = ?",
                (current["source"], current["subject"]),
            ).fetchone() is not None
        record = current

        def sandbox_write_allowed(connection: sqlite3.Connection) -> bool:
            return self._write_candidate(connection, record)["status"] == "applying"

        def connector_write_allowed() -> bool:
            with self.store.transaction(write=False) as connection:
                return sandbox_write_allowed(connection)

        # Connector writes are idempotent because a retry and a worker can overlap.
        external_ref = "wb-" + digest([self.workspace, record["source"], record["subject"], record["action"]["type"]])
        try:
            fields = record["action"]["fields"]
            if self._connector is not None:
                receipt = write_and_verify(self._connector, self._target, external_ref, fields,
                                           before_write=connector_write_allowed)
            elif authoritative:
                receipt = self.store.create_draft(external_ref, fields, before_write=sandbox_write_allowed)
            else:
                receipt = self.store.create_draft(external_ref, fields)
        except TimeoutError:
            raise WorkbenchError("outcome_unknown_retry_same_approval", 503) from None
        except WorkbenchError as error:
            if str(error) in {"draft_readback_conflict", "draft_conflict"}:
                with self.store.transaction() as connection:
                    current = self._read_exact_execution(connection, record)
                    # The existing reconciliation flag also holds definitive ERP conflicts.
                    current["crm_reconciliation_required"] = True
                    self.store.put_case(connection, current)
            raise
        with self.store.transaction() as connection:
            current = self._read_exact_execution(connection, record)
            if current["status"] == "applying":
                current.update(status="executed", receipt=receipt)
                self.store.put_case(connection, current)
        return current

    def feedback(self, case_id: str, actor: WorkActor, *, rating: str, comment: str) -> None:
        self.check_access(actor)
        if rating not in FEEDBACK or len(comment) > 1000:
            raise WorkbenchError("invalid_feedback", 422)
        _check_utf8(comment)
        with self.store.transaction() as connection:
            self._load_visible_case(connection, case_id, actor)
            connection.execute(
                """INSERT INTO feedback(case_id,actor,rating,comment,created_at) VALUES (?,?,?,?,?)
                   ON CONFLICT(case_id,actor) DO UPDATE SET rating=excluded.rating,
                       comment=excluded.comment, created_at=excluded.created_at""",
                (case_id, actor.actor, rating, comment.strip(), self._time()),
            )

    def history(self, case_id: str, actor: WorkActor) -> dict[str, Any]:
        """Decision evidence plus feedback summaries scoped to the sandbox workspace."""
        self.check_access(actor)
        with self.store.transaction(write=False) as connection:
            record = self._load_visible_case(connection, case_id, actor)
            decisions = [dict(row) for row in connection.execute(
                """SELECT d.revision, d.proposal_hash, d.actor, d.decision, d.rationale, d.created_at,
                          p.metadata AS provenance
                   FROM decisions AS d LEFT JOIN approval_provenance AS p
                     ON p.case_id = d.case_id AND p.revision = d.revision
                   WHERE d.case_id = ? ORDER BY d.revision DESC LIMIT 100""",
                (case_id,),
            )]
            counts = {row["rating"]: row["total"] for row in connection.execute(
                "SELECT rating, count(*) AS total FROM feedback WHERE case_id = ? GROUP BY rating",
                (case_id,),
            )}
            own_feedback = connection.execute(
                "SELECT rating, comment, created_at FROM feedback WHERE case_id = ? AND actor = ?",
                (case_id, actor.actor),
            ).fetchone()
        for item in decisions:
            provenance = item.pop("provenance")
            if item["actor"] == actor.actor or actor.role_for(ASSESSMENT):
                item["provenance"] = json.loads(provenance) if provenance else {"channel": "companion"}
            else:
                item.pop("actor")
                item.pop("rationale")
        result = {"case_id": case_id, "status": record["status"], "receipt": record["receipt"],
                  "decisions": decisions, "feedback_counts": counts,
                  "my_feedback": dict(own_feedback) if own_feedback else None}
        if record.get("crm_reconciliation_required"):
            result["reconciliation_required"] = True
        return result

    def _review(self, actor: WorkActor, subject: str, title: str, kind: str, reason: str,
                evidence: list[str], *, private: bool = False) -> dict[str, Any]:
        with self.store.transaction() as connection:
            previous = self._load_visible_subject(connection, "workbench-review", subject, actor)
            if previous is not None:
                if private and "private" not in previous:
                    raise WorkbenchError("review_subject_conflict", 409)
                return previous
            record = self._new_case(case_id=uuid.uuid4().hex, source="workbench-review", subject=subject,
                                    title=title, kind=kind, reason=reason, evidence=evidence, actor=actor,
                                    private=private)
            self.store.put_case(connection, record)
        return record

    def seed(self) -> None:
        if (self._seeded or self.workspace != WORKSPACE or self._connector is not None
                or self._crm_source != _SIMULATED_CRM_SOURCE or self._crm_sources):
            return
        with self._seed_lock:
            if not self._seeded:
                self._seed_cases()
                self._seeded = True

    def _seed_cases(self) -> None:
        system = Actor("sandbox", frozenset({"admin"}))
        self._submit({"specversion": "1.0", "source": _SIMULATED_CRM_SOURCE, "id": DEMO_EVENT_ID,
                     "type": "crm.opportunity.won", "subject": DEMO_SUBJECT,
                     "data": {"title": "Atelier Acme · ERP draft order", "customer_name": "Atelier Acme",
                              "customer_ref": "CRM-ACME", "amount_minor": 124000,
                              "currency": "EUR", "record_version": 1}}, system, seed=True)
        self._review(system, "demo-strategy", "Review an exceptional discount", "strategic",
                     "The proposed discount exceeds the documented authority.",
                     ["Sales threshold: 10%", "Proposed exception: 15%"])
        self._review(system, "demo-undocumented", "Supplier exception without a documented procedure", "undocumented",
                     "The ERP record is missing the required supplier reference.",
                     ["A human must decide which document establishes the supplier identity."])

    def chat(self, actor: WorkActor, *, agent: str, message: str, case_id: str | None = None) -> dict[str, Any]:
        self.check_access(actor)
        message = message.strip()
        if agent not in AGENTS or not message or len(message) > 1000:
            raise WorkbenchError("invalid_chat", 422)
        _check_utf8(message)
        record = None
        if case_id:
            with self.store.transaction(write=False) as connection:
                record = self._load_visible_case(connection, case_id, actor)
        strategic = bool(STRATEGIC.search(message))
        if strategic or record is None or ACTION_REQUEST.search(message) or not INFORMATIONAL.search(message):
            kind = "strategic" if strategic else "undocumented"
            title = "Strategic question for human review" if strategic else "Question without a documented answer"
            review = self._review(actor, uuid.uuid4().hex, title, kind, message,
                                  [f"Agent: {agent}"] + ([f"Related case: {case_id}"] if case_id else []),
                                  private=actor.authenticated)
            return {"answer": "A human review was opened. No business action was executed.",
                    "escalated": True, "case_id": review["id"], "source": "simulation"}
        if record["kind"] != "action":
            return {"answer": record["reason"], "escalated": True, "case_id": record["id"], "source": "simulation"}
        use_local = self.advisor is not None and self.mode == "protected_demo" and actor.authenticated
        candidate = ModelRoute("chat-sensitive" if use_local else "demo-rules", "local", "sandbox", 0, 50, 1)
        route = select_route(RoutePolicy("C3", frozenset({"sandbox"}), 0, 500), [candidate])
        if route.model_id is None:
            raise WorkbenchError("no_admissible_agent", 503)
        if use_local:
            answer = self.advisor(agent, message, record)
        else:
            answer = f"{record['title']} · {record['status']}. {record['reason']} " + " ".join(record["evidence"])
        return {"answer": answer, "escalated": False, "case_id": record["id"],
                "source": "local" if use_local else "simulation",
                "route": {"model_id": route.model_id, "reason": route.reason}}
