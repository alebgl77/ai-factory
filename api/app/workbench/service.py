"""Conservative decision workflow for a single synthetic workspace."""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from ..audit import digest
from ..security import ASSESSMENT, READ, Principal
from .adapters import normalize_opportunity_event
from .algorithms import ModelRoute, RoutePolicy, match_customer, review_priority, select_route
from .store import Store, WorkbenchError

WORKSPACE = "demo"
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
                 advisor: Callable[[str, str, dict[str, Any]], str] | None = None) -> None:
        self.store = Store(path)
        self.mode = mode
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self.advisor = advisor
        self._seeded = False
        self._seed_lock = threading.Lock()

    def _time(self) -> str:
        return self.clock().astimezone(dt.timezone.utc).isoformat()

    def check_access(self, actor: Principal) -> None:
        if self.mode == "protected_demo" and not actor.authenticated:
            raise WorkbenchError("authentication_required", 401)
        if not actor.role_for(READ):
            raise WorkbenchError("forbidden", 403)

    def capabilities(self, actor: Principal) -> dict[str, bool | str]:
        self.check_access(actor)
        return {"can_submit": bool(actor.roles & SUBMIT_ROLES),
                "can_decide": bool(actor.role_for(ASSESSMENT)),
                "chat_source": "local" if self.advisor is not None else "simulation"}

    @staticmethod
    def _owns(record: dict[str, Any], actor: Principal) -> bool:
        return actor.authenticated and bool(actor.ids & set(record["proposer_ids"]))

    @staticmethod
    def _priority(record: dict[str, Any], now: dt.datetime) -> int:
        age = (now - dt.datetime.fromisoformat(record["created_at"])).total_seconds() / 3600
        amount = record["action"]["fields"]["amount_minor"] if record["action"] else 0
        return review_priority(record["kind"], max(0.0, age), amount)

    def _public(self, record: dict[str, Any], actor: Principal, now: dt.datetime | None = None) -> dict[str, Any]:
        now = now or self.clock()
        result = {key: record[key] for key in PUBLIC_CASE_FIELDS}
        result["priority"] = self._priority(record, now)
        result["can_decide"] = (record["status"] in QUEUE_STATES and bool(actor.role_for(ASSESSMENT))
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

    def cases(self, actor: Principal) -> list[dict[str, Any]]:
        self.check_access(actor)
        now = self.clock()
        with self.store.transaction(write=False) as connection:
            connection.create_function("review_priority", 1, lambda raw: self._priority(json.loads(raw), now),
                                       deterministic=True)
            rows = connection.execute(
                """SELECT record FROM cases
                   ORDER BY CASE WHEN status IN ('waiting_review','applying') THEN 0 ELSE 1 END,
                            review_priority(record) DESC, json_extract(record,'$.created_at'), id
                   LIMIT 200"""
            ).fetchall()
        return [self._public(json.loads(row["record"]), actor, now) for row in rows]

    def _new_case(self, *, case_id: str, source: str, subject: str, title: str, kind: str,
                  reason: str, evidence: list[str], actor: Principal, action: dict | None = None,
                  revision: int = 1, record_version: int = 0, source_payload_hash: str = "") -> dict:
        now = self.clock().astimezone(dt.timezone.utc)
        expires_at = (now + APPROVAL_LIFETIME).isoformat() if action else None
        proposal_hash = digest({"workspace": WORKSPACE, "case": case_id, "revision": revision,
                                "source": source, "subject": subject, "record_version": record_version,
                                "kind": kind, "title": title, "action": action, "reason": reason,
                                "policy_version": 1, "expires_at": expires_at,
                                "evidence": evidence})
        return {"id": case_id, "source": source, "subject": subject, "title": title, "kind": kind,
                "status": "waiting_review", "revision": revision, "record_version": record_version,
                "proposal_hash": proposal_hash, "source_payload_hash": source_payload_hash,
                "proposer_ids": sorted(actor.ids), "reason": reason, "action": action, "evidence": evidence,
                "receipt": None, "decision": None, "created_at": now.isoformat(), "expires_at": expires_at}

    def submit(self, payload: dict[str, Any], actor: Principal) -> dict[str, Any]:
        return self._submit(payload, actor, seed=False)

    def _submit(self, payload: dict[str, Any], actor: Principal, *, seed: bool) -> dict[str, Any]:
        self.check_access(actor)
        if not actor.roles & SUBMIT_ROLES:
            raise WorkbenchError("forbidden", 403)
        try:
            event = normalize_opportunity_event(payload, tenant=WORKSPACE)
        except (ValueError, TypeError):
            raise WorkbenchError("invalid_event", 422) from None
        if event.source != "crm-sim":
            raise WorkbenchError("invalid_event", 422)
        if not seed and (event.id == DEMO_EVENT_ID or event.subject == DEMO_SUBJECT):
            raise WorkbenchError("reserved_demo_identity", 422)
        source_payload_hash = event.record_fingerprint
        match = match_customer(
            {"customer_name": event.customer_name, "customer_ref": event.customer_ref}, CUSTOMERS,
            tenant=WORKSPACE, allowed_ids={row["id"] for row in CUSTOMERS},
        )
        fields = {"customer_id": match.candidate_id, "crm_opportunity": event.subject,
                  "amount_minor": event.amount_minor, "currency": event.currency}
        action = {"type": "erp.create_draft_order", "fields": fields} if match.status == "matched" else None
        kind = "action" if action else "undocumented"
        reason = ("Customer matched. Draft ERP order requires approval." if action
                  else "No unambiguous customer match. Human review is required.")
        evidence = [f"CRM: {event.source} / {event.subject} / v{event.record_version}",
                    f"Customer match: {match.status} ({match.score:.2f})",
                    f"Amount: {event.amount_minor} minor units {event.currency}"]
        with self.store.transaction() as connection:
            previous_event = connection.execute(
                "SELECT fingerprint, case_id FROM events WHERE source = ? AND id = ?", (event.source, event.id)
            ).fetchone()
            if previous_event:
                if previous_event["fingerprint"] != event.fingerprint:
                    raise WorkbenchError("event_conflict", 409)
                return self._public(self.store.load_case(connection, previous_event["case_id"]), actor)
            row = connection.execute(
                "SELECT record FROM cases WHERE source = ? AND subject = ?", (event.source, event.subject)
            ).fetchone()
            previous = json.loads(row["record"]) if row else None
            if previous and event.record_version < previous["record_version"]:
                raise WorkbenchError("stale_event", 409)
            if previous and event.record_version == previous["record_version"]:
                if previous["source_payload_hash"] != source_payload_hash:
                    raise WorkbenchError("event_revision_conflict", 409)
                record = previous
            else:
                if previous and previous["status"] in {"applying", "executed"}:
                    raise WorkbenchError("reconciliation_required", 409)
                record = self._new_case(
                    case_id=previous["id"] if previous else uuid.uuid4().hex, source=event.source,
                    subject=event.subject, title=event.title, kind=kind, reason=reason, evidence=evidence,
                    actor=actor, action=action, revision=previous["revision"] + 1 if previous else 1,
                    record_version=event.record_version, source_payload_hash=source_payload_hash,
                )
                self.store.put_case(connection, record)
            connection.execute("INSERT INTO events(source,id,fingerprint,case_id) VALUES (?,?,?,?)",
                               (event.source, event.id, event.fingerprint, record["id"]))
        return self._public(record, actor)

    def decide(self, case_id: str, actor: Principal, *, decision: str, revision: int,
               proposal_hash: str, rationale: str) -> dict[str, Any]:
        self.check_access(actor)
        if not actor.role_for(ASSESSMENT):
            raise WorkbenchError("forbidden", 403)
        if decision not in DECISIONS or not rationale.strip() or len(rationale) > 1000:
            raise WorkbenchError("invalid_decision", 422)
        _check_utf8(rationale)
        with self.store.transaction() as connection:
            record = self.store.load_case(connection, case_id)
            if revision != record["revision"] or proposal_hash != record["proposal_hash"]:
                raise WorkbenchError("stale_proposal", 409)
            if decision == "approve" and self._owns(record, actor):
                raise WorkbenchError("self_approval_forbidden", 403)
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
                connection.execute(
                    """INSERT INTO decisions(case_id,revision,actor,proposal_hash,decision,rationale,created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (case_id, revision, actor.actor, proposal_hash, decision, rationale.strip(), self._time()),
                )
                self.store.put_case(connection, record)
        if record["status"] != "applying":
            return self._public(record, actor)

        # The approved intention is committed before contacting the adapter. If the response is
        # lost, a retry uses the same business identity and reconciles the existing draft.
        external_ref = "wb-" + digest([WORKSPACE, record["source"], record["subject"], record["action"]["type"]])
        try:
            receipt = self.store.create_draft(external_ref, record["action"]["fields"])
        except TimeoutError:
            raise WorkbenchError("outcome_unknown_retry_same_approval", 503) from None
        with self.store.transaction() as connection:
            record = self.store.load_case(connection, case_id)
            if record["status"] == "applying":
                record.update(status="executed", receipt=receipt)
                self.store.put_case(connection, record)
        return self._public(record, actor)

    def feedback(self, case_id: str, actor: Principal, *, rating: str, comment: str) -> None:
        self.check_access(actor)
        if rating not in FEEDBACK or len(comment) > 1000:
            raise WorkbenchError("invalid_feedback", 422)
        _check_utf8(comment)
        with self.store.transaction() as connection:
            self.store.load_case(connection, case_id)
            connection.execute(
                """INSERT INTO feedback(case_id,actor,rating,comment,created_at) VALUES (?,?,?,?,?)
                   ON CONFLICT(case_id,actor) DO UPDATE SET rating=excluded.rating,
                       comment=excluded.comment, created_at=excluded.created_at""",
                (case_id, actor.actor, rating, comment.strip(), self._time()),
            )

    def history(self, case_id: str, actor: Principal) -> dict[str, Any]:
        """Decision evidence plus feedback summaries scoped to the sandbox workspace."""
        self.check_access(actor)
        with self.store.transaction(write=False) as connection:
            record = self.store.load_case(connection, case_id)
            decisions = [dict(row) for row in connection.execute(
                """SELECT revision, proposal_hash, actor, decision, rationale, created_at
                   FROM decisions WHERE case_id = ? ORDER BY revision DESC LIMIT 100""",
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
            if item["actor"] != actor.actor and not actor.role_for(ASSESSMENT):
                item.pop("actor")
                item.pop("rationale")
        return {"case_id": case_id, "status": record["status"], "receipt": record["receipt"],
                "decisions": decisions, "feedback_counts": counts,
                "my_feedback": dict(own_feedback) if own_feedback else None}

    def _review(self, actor: Principal, subject: str, title: str, kind: str, reason: str,
                evidence: list[str]) -> dict[str, Any]:
        with self.store.transaction() as connection:
            row = connection.execute("SELECT record FROM cases WHERE source = ? AND subject = ?",
                                     ("workbench-review", subject)).fetchone()
            if row:
                return json.loads(row["record"])
            record = self._new_case(case_id=uuid.uuid4().hex, source="workbench-review", subject=subject,
                                    title=title, kind=kind, reason=reason, evidence=evidence, actor=actor)
            self.store.put_case(connection, record)
        return record

    def seed(self) -> None:
        if self._seeded:
            return
        with self._seed_lock:
            if not self._seeded:
                self._seed_cases()
                self._seeded = True

    def _seed_cases(self) -> None:
        system = Principal("sandbox", frozenset({"admin"}), True, "internal", user="sandbox")
        self._submit({"specversion": "1.0", "source": "crm-sim", "id": DEMO_EVENT_ID,
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

    def chat(self, actor: Principal, *, agent: str, message: str, case_id: str | None = None) -> dict[str, Any]:
        self.check_access(actor)
        message = message.strip()
        if agent not in AGENTS or not message or len(message) > 1000:
            raise WorkbenchError("invalid_chat", 422)
        _check_utf8(message)
        record = None
        if case_id:
            with self.store.transaction(write=False) as connection:
                record = self.store.load_case(connection, case_id)
        strategic = bool(STRATEGIC.search(message))
        if strategic or record is None or ACTION_REQUEST.search(message) or not INFORMATIONAL.search(message):
            kind = "strategic" if strategic else "undocumented"
            title = "Strategic question for human review" if strategic else "Question without a documented answer"
            review = self._review(actor, uuid.uuid4().hex, title, kind, message,
                                  [f"Agent: {agent}"] + ([f"Related case: {case_id}"] if case_id else []))
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
