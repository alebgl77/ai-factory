"""Run a synthetic governance and ERP recovery scenario using the Workbench SDK."""

from __future__ import annotations

import datetime as dt
import json
import secrets
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from ai_factory_workbench import Actor, AdapterCapabilities, DraftReadback, Workbench, WorkbenchError
from ai_factory_workbench.approvals import ApprovalRelay, RelayAuthority, sign_approval
from ai_factory_workbench.profiles import compare_policies, get_profile

WORKSPACE = "example-factory"
NOW = dt.datetime(2026, 11, 6, 10, tzinfo=dt.timezone.utc)
PROPOSER = Actor("example-editor", frozenset({"editor"}))
REVIEWER = Actor("example-reviewer", frozenset({"approver"}))
SERVICE = Actor("example-service", frozenset({"approver"}))
EVIDENCE = {
    "artifact_ref": "registry.example/model@v2",
    "evaluation_ref": "evaluations/example-v2",
    "risk_summary": "Confirm that all sensitive queries stay in the approved environment.",
    "rollback_plan": "Restore model v1 and review the affected requests.",
}
CUSTOMERS = ({"id": "erp-example", "tenant": WORKSPACE, "name": "Example Atelier", "reference": "CRM-EXAMPLE"},)


class ExampleERP:
    """Separate disposable store with an atomic unique draft key and durable readback."""

    target = "example-erp"
    capabilities = AdapterCapabilities(draft_writes=True, idempotency_keys=True)

    def __init__(self, path: Path, *, lose_response: bool = False) -> None:
        self.path = path
        self.lose_response = lose_response
        with self._transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS drafts (reference TEXT PRIMARY KEY, fields TEXT NOT NULL)")

    @contextmanager
    def _transaction(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            yield db

    def ensure_draft(self, external_ref: str, fields: dict) -> None:
        payload = json.dumps(fields, sort_keys=True)
        with self._transaction() as db:
            db.execute("INSERT INTO drafts VALUES (?, ?) ON CONFLICT (reference) DO NOTHING", (external_ref, payload))
            stored = db.execute("SELECT fields FROM drafts WHERE reference = ?", (external_ref,)).fetchone()
            if stored != (payload,):
                raise WorkbenchError("draft_readback_conflict", 409)
        if self.lose_response:
            self.lose_response = False
            raise TimeoutError("The synthetic ERP committed before losing the response.")

    def read_draft(self, external_ref: str) -> DraftReadback | None:
        with self._transaction() as db:
            row = db.execute("SELECT fields FROM drafts WHERE reference = ?", (external_ref,)).fetchone()
        return DraftReadback(f"draft-{external_ref}", external_ref, json.loads(row[0])) if row else None

    def draft_count(self) -> int:
        with self._transaction() as db:
            return db.execute("SELECT count(*) FROM drafts").fetchone()[0]


def approval_envelope(engine: Workbench, case: dict, authority: RelayAuthority, identity: str) -> dict:
    issued = int(NOW.timestamp())
    return {
        "workspace": engine.workspace, "issuer": authority.issuer, "channel": authority.channel,
        "identity": identity, "case_id": case["id"], "revision": case["revision"],
        "proposal_hash": case["proposal_hash"], "decision": "approve",
        "rationale": "Independent review of the evidence and rollback plan.",
        "issued_at": issued, "expires_at": issued + 60,
        "request_id": f"example-{authority.channel}-{identity}",
    }


def approve_in_host(engine: Workbench, case: dict) -> dict:
    return engine.decide(case["id"], REVIEWER, decision="approve", revision=case["revision"],
                         proposal_hash=case["proposal_hash"], rationale="Independent review complete.")


def run(directory: Path) -> dict:
    database, provider_database = directory / "workbench.sqlite3", directory / "erp.sqlite3"
    provider = ExampleERP(provider_database, lose_response=True)

    def open_workbench(erp: ExampleERP) -> Workbench:
        return Workbench(database, mode="protected_demo", clock=lambda: NOW, workspace=WORKSPACE,
                         connector=erp, customers=CUSTOMERS, crm_source="crm-example", defer_execution=True)

    engine = open_workbench(provider)
    profile = get_profile("governance")
    authorities = (
        RelayAuthority("example-slack", secrets.token_bytes(32), "slack", {"T-DEMO:U-REVIEWER": REVIEWER}),
        RelayAuthority("example-teams", secrets.token_bytes(32), "teams", {"tenant-demo:reviewer": REVIEWER}),
        RelayAuthority("example-internal", secrets.token_bytes(32), "service", {"employee": REVIEWER, "worker": SERVICE},
                       service_identities=frozenset({"worker"})),
    )
    relay = ApprovalRelay(engine, profile, authorities)

    def prepare(label: str, evidence: dict | None = None) -> dict:
        case = profile.prepare(engine, PROPOSER, subject=f"example-release/{label}", title="Review model v2",
                               reason="Independent release review.", evidence=EVIDENCE if evidence is None else evidence)
        assert case["action"] is None and case["profile"] == profile.snapshot()
        return case

    direct = prepare("companion")
    revised = prepare("companion", {**EVIDENCE, "evaluation_ref": "evaluations/example-v2-recheck"})
    assert revised["revision"] == direct["revision"] + 1
    try:
        approve_in_host(engine, direct)
    except WorkbenchError as error:
        assert str(error) == "stale_proposal"
    else:
        raise AssertionError("A stale review was accepted.")
    direct_outcome = approve_in_host(engine, revised)
    assert direct_outcome["status"] == "decided"
    outcomes = {"companion": engine.history(revised["id"], REVIEWER)["decisions"][0]}

    for authority, identity in zip(authorities, ("T-DEMO:U-REVIEWER", "tenant-demo:reviewer", "employee"), strict=True):
        case = prepare(authority.channel)
        envelope = approval_envelope(engine, case, authority, identity)
        relayed = relay.decide(envelope, sign_approval(authority.key, envelope))
        assert relayed["status"] == "decided"
        outcome, = engine.history(case["id"], REVIEWER)["decisions"]
        assert outcome["provenance"]["channel"] == authority.channel and outcome["actor"] == REVIEWER.actor
        outcomes[authority.channel] = outcome

    machine_case = prepare("machine-only")
    service_authority = authorities[-1]
    machine_envelope = approval_envelope(engine, machine_case, service_authority, "worker")
    try:
        relay.decide(machine_envelope, sign_approval(service_authority.key, machine_envelope))
    except WorkbenchError as error:
        assert str(error) == "human_approval_required"
    else:
        raise AssertionError("A machine-only governance approval was accepted.")
    assert engine.history(machine_case["id"], REVIEWER)["decisions"] == []

    queue_before_replay = engine.cases(REVIEWER)
    policy_report = compare_policies(profile, get_profile("operations"))
    assert policy_report["changed_count"] > 0 and engine.cases(REVIEWER) == queue_before_replay

    event = {
        "specversion": "1.0", "source": "crm-example", "id": "example-delivery", "type": "crm.opportunity.won",
        "subject": "example-opportunity", "data": {
            "title": "Example Atelier order", "customer_name": "Example Atelier", "customer_ref": "CRM-EXAMPLE",
            "amount_minor": 128_900, "currency": "EUR", "record_version": 1,
        },
    }
    order = engine.submit(event, PROPOSER, profile=profile.snapshot())
    assert order["profile"] == profile.snapshot()
    queued = approve_in_host(engine, order)
    assert queued["status"] == "applying" and provider.draft_count() == 0
    assert engine.pending_execution_ids() == [order["id"]]

    revised_event = {**event, "id": "example-delivery-2",
                     "data": {**event["data"], "amount_minor": 129_900, "record_version": 2}}
    revised_order = engine.submit(revised_event, PROPOSER, profile=profile.snapshot())
    assert revised_order["revision"] == order["revision"] + 1
    assert revised_order["status"] == "waiting_review" and engine.pending_execution_ids() == []
    assert provider.draft_count() == 0
    queued = approve_in_host(engine, revised_order)
    assert queued["status"] == "applying"
    try:
        engine.reconcile_pending(order["id"])
    except WorkbenchError as error:
        assert str(error) == "outcome_unknown_retry_same_approval" and provider.draft_count() == 1
    else:
        raise AssertionError("The simulated lost response was not observed.")
    reopened_provider = ExampleERP(provider_database)
    reopened = open_workbench(reopened_provider)
    assert reopened.pending_execution_ids() == [order["id"]]
    recovered = reopened.reconcile_pending(order["id"])
    assert recovered["status"] == "executed" and reopened_provider.draft_count() == 1
    assert reopened.reconcile_pending(order["id"]) == recovered and reopened.pending_execution_ids() == []
    draft = reopened_provider.read_draft(recovered["receipt"]["external_ref"])
    assert draft is not None and draft.fields["amount_minor"] == 129_900
    return {
        "scenario": "synthetic_local",
        "profile": profile.snapshot(),
        "governance_approvals": outcomes,
        "stale_revision": "blocked",
        "machine_only_governance": "blocked",
        "policy_comparison": policy_report,
        "erp_recovery": {**recovered, "draft_count": reopened_provider.draft_count(),
                         "unclaimed_revision_superseded": True, "lost_response_recovered": True},
    }


if __name__ == "__main__":
    with TemporaryDirectory(prefix="ai-factory-example-") as temporary:
        print(json.dumps(run(Path(temporary)), indent=2, ensure_ascii=True))
