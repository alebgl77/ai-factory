from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest

from ai_factory_workbench import Actor, AdapterCapabilities, DraftReadback, Workbench, WorkbenchError
from ai_factory_workbench.conformance import probe_draft_connector
from app.workbench.service import Workbench as LegacyWorkbench


EDITOR = Actor("editor", frozenset({"editor"}))
APPROVER = Actor("reviewer", frozenset({"approver"}))
NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
WORKSPACE = "operations-fixture"
PROFILE = {"id": "governance", "version": "1", "fingerprint": "a" * 64}
CUSTOMERS = [{"id": "partner-test", "name": "Test Partner", "reference": "CRM-TEST", "tenant": WORKSPACE}]
EVENT = {
    "specversion": "1.0", "type": "crm.opportunity.won", "source": "crm-test",
    "id": "order-delivery-1", "subject": "order-1",
    "data": {"title": "Prepare draft", "customer_name": "Test Partner", "customer_ref": "CRM-TEST",
             "amount_minor": 2300, "currency": "EUR", "record_version": 1},
}


def review(engine, *, subject="policy-1", profile=PROFILE, actor=EDITOR):
    return engine.propose_review(actor, subject=subject, title="Policy change", kind="strategic",
                                 reason="Needs a separate review", evidence=["Request T-7"], profile=profile)


def approve(engine, case, actor=APPROVER, **kwargs):
    return engine.decide(case["id"], actor, revision=case["revision"], proposal_hash=case["proposal_hash"],
                         decision="approve", rationale="Reviewed the evidence.", **kwargs)


def test_legacy_import_and_workspace_isolation(tmp_path):
    assert LegacyWorkbench is Workbench
    path = tmp_path / "state.sqlite3"
    first = Workbench(path, workspace="alpha")
    case = review(first)
    assert Workbench(path, workspace="alpha").get_case(case["id"], APPROVER) == first.get_case(case["id"], APPROVER)
    with pytest.raises(WorkbenchError, match="workspace_mismatch"):
        Workbench(path, workspace="beta").get_case(case["id"], APPROVER)
    with pytest.raises(WorkbenchError, match="case_not_found"):
        Workbench(tmp_path / "beta.sqlite3", workspace="beta").get_case(case["id"], APPROVER)
    with pytest.raises(WorkbenchError, match="authentication_required"):
        first.get_case(case["id"], Actor("anonymous", frozenset({"admin"}), authenticated=False))


def test_legacy_demo_binding_never_adopted_by_other_workspace(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    before = Workbench(path)
    before.seed()
    case = before.cases(APPROVER)[0]
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DROP TABLE approval_provenance")
        connection.execute("DROP TABLE workspace_settings")
    with pytest.raises(WorkbenchError, match="workspace_binding_required"):
        Workbench(path, workspace="alpha").get_case(case["id"], APPROVER)
    assert Workbench(path).get_case(case["id"], APPROVER)["proposal_hash"] == case["proposal_hash"]


def test_first_workspace_binding_has_one_winner(tmp_path):
    path = tmp_path / "race.sqlite3"
    start = Barrier(2)

    def bind(workspace):
        start.wait(timeout=5)
        try:
            return bool(review(Workbench(path, workspace=workspace)))
        except WorkbenchError as error:
            assert str(error) == "workspace_mismatch"
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(bind, ("alpha", "beta"))) == [False, True]


def test_profile_is_bound_to_exact_revision_and_proposer(tmp_path):
    engine = Workbench(tmp_path / "workbench.sqlite3", workspace="governance")
    old = review(engine)
    assert old["profile"] == PROFILE
    assert review(engine)["proposal_hash"] == old["proposal_hash"]
    with pytest.raises(WorkbenchError, match="review_subject_conflict"):
        review(engine, actor=Actor("other-editor", frozenset({"editor"})))
    new = review(engine, profile={**PROFILE, "version": "2", "fingerprint": "b" * 64})
    assert new["id"] == old["id"] and new["revision"] == old["revision"] + 1
    assert new["proposal_hash"] != old["proposal_hash"]
    with pytest.raises(WorkbenchError, match="stale_proposal"):
        approve(engine, old)
    with pytest.raises(WorkbenchError, match="self_approval_forbidden"):
        approve(engine, new, Actor("alias", frozenset({"approver"}), subject=EDITOR.actor))
    assert approve(engine, new)["status"] == "decided"
    with pytest.raises(WorkbenchError, match="already_decided"):
        review(engine, profile=PROFILE)


def test_approval_replay_and_provenance_are_atomic_and_scoped(tmp_path):
    engine = Workbench(tmp_path / "proof.sqlite3", clock=lambda: NOW, workspace="governance")
    first = review(engine)
    second = review(engine, subject="policy-2")
    proof = {"channel": "teams", "issuer": "internal-relay", "identity": "verified-human",
             "request_id": "request-1", "proof_sha256": "f" * 64,
             "expires_at": (NOW + timedelta(minutes=2)).isoformat()}
    assert approve(engine, first, provenance=proof)["status"] == "decided"
    assert approve(engine, first, provenance=proof)["status"] == "decided"
    with pytest.raises(WorkbenchError, match="approval_replay"):
        approve(engine, second, provenance={**proof, "proof_sha256": "e" * 64})
    with pytest.raises(WorkbenchError, match="approval_proof_expired"):
        approve(engine, second, provenance={**proof, "request_id": "expired", "expires_at": NOW.isoformat()})
    assert engine.get_case(second["id"], APPROVER)["status"] == "waiting_review"
    assert engine.history(first["id"], APPROVER)["decisions"][0]["provenance"] == proof
    visible = engine.history(first["id"], EDITOR)["decisions"][0]
    assert "provenance" not in visible and "actor" not in visible and "rationale" not in visible


class FixtureERP:
    target = "fixture-erp"
    capabilities = AdapterCapabilities(draft_writes=True, idempotency_keys=True)

    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS drafts (ref TEXT PRIMARY KEY, fields TEXT NOT NULL)")

    def ensure_draft(self, external_ref, fields):
        payload = json.dumps(fields, sort_keys=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("INSERT OR IGNORE INTO drafts VALUES(?,?)", (external_ref, payload))
            stored = connection.execute("SELECT fields FROM drafts WHERE ref = ?", (external_ref,)).fetchone()[0]
            if stored != payload:
                raise AssertionError("idempotency collision")

    def read_draft(self, external_ref):
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute("SELECT fields FROM drafts WHERE ref = ?", (external_ref,)).fetchone()
        return DraftReadback("draft-" + external_ref[-16:], external_ref, json.loads(row[0])) if row else None

    def count(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute("SELECT COUNT(*) FROM drafts").fetchone()[0]


def test_connector_conformance_reads_real_provider_state(tmp_path):
    provider_path = tmp_path / "provider.sqlite3"
    provider = FixtureERP(provider_path)
    result = probe_draft_connector(lambda: FixtureERP(provider_path), provider.count, event=EVENT,
                                   customers=CUSTOMERS, workspace=WORKSPACE, disposable=True)
    assert result == {"lost_ack_reconciled": True, "concurrent_recovery_verified": True, "provider_drafts": 2}


def test_deferred_queue_and_connector_target_are_durable(tmp_path):
    path = tmp_path / "workbench.sqlite3"
    provider = FixtureERP(tmp_path / "provider.sqlite3")
    engine = Workbench(path, workspace=WORKSPACE, connector=provider, customers=CUSTOMERS,
                       crm_source="crm-test", defer_execution=True)
    case = engine.submit(EVENT, EDITOR)
    with pytest.raises(WorkbenchError, match="no_pending_execution"):
        engine.reconcile_pending(case["id"])
    assert approve(engine, case)["status"] == "applying"
    assert provider.count() == 0

    class OtherERP(FixtureERP):
        target = "other-erp"

    other = OtherERP(tmp_path / "other.sqlite3")
    with pytest.raises(WorkbenchError, match="connector_target_mismatch"):
        Workbench(path, workspace=WORKSPACE, connector=other).reconcile_pending(case["id"])
    assert other.count() == 0
    restarted = Workbench(path, workspace=WORKSPACE, connector=FixtureERP(provider.path))
    assert restarted.pending_execution_ids() == [case["id"]]
    result = restarted.reconcile_pending(case["id"])
    assert result["status"] == "executed" and result["receipt"]["system"] == provider.target
    assert restarted.reconcile_pending(case["id"]) == result
    assert restarted.pending_execution_ids() == [] and provider.count() == 1


def test_readback_conflict_preserves_unknown_result_and_never_overwrites(tmp_path):
    class ConflictingERP(FixtureERP):
        def read_draft(self, external_ref):
            return DraftReadback("existing", external_ref, {"wrong": "data"})

        def ensure_draft(self, external_ref, fields):
            pytest.fail("a mismatching draft must not be overwritten")

    provider = ConflictingERP(tmp_path / "provider.sqlite3")
    engine = Workbench(tmp_path / "workbench.sqlite3", workspace=WORKSPACE, connector=provider,
                       customers=CUSTOMERS, crm_source="crm-test")
    case = engine.submit(EVENT, EDITOR)
    with pytest.raises(WorkbenchError, match="draft_readback_conflict"):
        approve(engine, case)
    assert engine.get_case(case["id"], APPROVER)["status"] == "applying"

    provider.capabilities = AdapterCapabilities(draft_writes=True)
    with pytest.raises(ValueError, match="atomic idempotent"):
        Workbench(tmp_path / "unsafe.sqlite3", connector=provider)


def _next_event():
    return {**EVENT, "id": "order-delivery-2", "data": {**EVENT["data"], "amount_minor": 4500, "record_version": 2}}


def test_new_revision_supersedes_only_unclaimed_deferred_approval(tmp_path, monkeypatch):
    provider = FixtureERP(tmp_path / "provider.sqlite3")
    engine = Workbench(tmp_path / "workbench.sqlite3", workspace=WORKSPACE, connector=provider,
                       customers=CUSTOMERS, crm_source="crm-test", defer_execution=True)
    first = engine.submit(EVENT, EDITOR)
    assert approve(engine, first)["status"] == "applying"
    assert provider.count() == 0

    original_apply = engine._apply

    def race_with_crm_event(record):
        engine.submit(_next_event(), EDITOR)
        return original_apply(record)

    # The worker already read the approval when the newer CRM event arrives.
    with monkeypatch.context() as patch:
        patch.setattr(engine, "_apply", race_with_crm_event)
        with pytest.raises(WorkbenchError, match="stale_proposal"):
            engine.reconcile_pending(first["id"])

    updated = engine.get_case(first["id"], APPROVER)
    assert updated["status"] == "waiting_review" and updated["revision"] == first["revision"] + 1
    assert engine.pending_execution_ids() == [] and provider.count() == 0
    with pytest.raises(WorkbenchError, match="stale_proposal"):
        approve(engine, first)
    assert engine.history(first["id"], APPROVER)["decisions"][0]["revision"] == first["revision"]

    approve(engine, updated)
    receipt = engine.reconcile_pending(updated["id"])["receipt"]
    assert provider.count() == 1
    assert provider.read_draft(receipt["external_ref"]).fields["amount_minor"] == 4500


@pytest.mark.parametrize("legacy", [False, True])
def test_attempted_or_legacy_execution_requires_reconciliation(tmp_path, monkeypatch, legacy):
    provider = FixtureERP(tmp_path / "provider.sqlite3")
    engine = Workbench(tmp_path / "workbench.sqlite3", workspace=WORKSPACE, connector=provider,
                       customers=CUSTOMERS, crm_source="crm-test")
    first = engine.submit(EVENT, EDITOR)
    original_write = provider.ensure_draft

    def lose_ack(external_ref, fields):
        original_write(external_ref, fields)
        raise TimeoutError

    with monkeypatch.context() as patch:
        patch.setattr(provider, "ensure_draft", lose_ack)
        with pytest.raises(WorkbenchError, match="outcome_unknown_retry_same_approval"):
            approve(engine, first)
    if legacy:
        with engine.store.transaction() as connection:
            record = engine.store.load_case(connection, first["id"])
            assert record.pop("execution_started") is True
            engine.store.put_case(connection, record)
    with pytest.raises(WorkbenchError, match="reconciliation_required"):
        engine.submit(_next_event(), EDITOR)
    assert engine.reconcile_pending(first["id"])["status"] == "executed"
    assert provider.count() == 1


def test_demo_guest_cannot_access_sdk_or_profile_bound_cases_in_shared_store(tmp_path):
    demo_customer = {**CUSTOMERS[0], "tenant": "demo"}
    options = {"customers": [demo_customer], "crm_source": "crm-test"}
    path = tmp_path / "demo.sqlite3"
    protected = Workbench(path, mode="protected_demo", **options)
    demo = Workbench(path, **options)
    guest = Actor("demo-guest", frozenset({"admin"}), authenticated=False)
    cases = [review(protected, profile=None), protected.submit(EVENT, EDITOR, profile=PROFILE)]
    legacy_event = {**EVENT, "id": "demo-legacy-event", "subject": "demo-legacy-opportunity"}
    legacy = demo.submit(legacy_event, guest)
    for index in range(200):
        review(protected, subject=f"private-priority-{index}")

    assert [case["id"] for case in demo.cases(guest)] == [legacy["id"]]
    for case in cases:
        case_id = case["id"]
        requests = (
            lambda: demo.get_case(case_id, guest),
            lambda: demo.history(case_id, guest),
            lambda: demo.feedback(case_id, guest, rating="helpful", comment="should not persist"),
            lambda: demo.chat(guest, agent="governance", message="What is the status?", case_id=case_id),
            lambda: approve(demo, case, guest),
        )
        for request in requests:
            with pytest.raises(WorkbenchError, match="case_not_found") as error:
                request()
            assert error.value.status == 404
        history = protected.history(case_id, APPROVER)
        assert history["decisions"] == [] and history["feedback_counts"] == {}

    conflicting_duplicate = {**EVENT, "data": {**EVENT["data"], "amount_minor": 9999}}
    for event in (EVENT, conflicting_duplicate, _next_event()):
        with pytest.raises(WorkbenchError, match="case_not_found"):
            demo.submit(event, guest)
    assert protected.get_case(cases[1]["id"], APPROVER)["revision"] == 1
    assert approve(demo, legacy, guest)["status"] == "executed"


@pytest.mark.parametrize("profile", [None, PROFILE])
def test_private_crm_revision_stays_private_when_profile_is_omitted(tmp_path, profile):
    demo_customer = {**CUSTOMERS[0], "tenant": "demo"}
    options = {"customers": [demo_customer], "crm_source": "crm-test"}
    path = tmp_path / "demo.sqlite3"
    protected = Workbench(path, mode="protected_demo", **options)
    demo = Workbench(path, **options)
    guest = Actor("demo-guest", frozenset({"admin"}), authenticated=False)

    # A profile in a demo-mode host and an unprofiled protected-mode record
    # must both stay private when a later update comes through the demo host.
    first = (demo if profile else protected).submit(EVENT, EDITOR, profile=profile)
    with pytest.raises(WorkbenchError, match="case_not_found"):
        demo.get_case(first["id"], guest)
    updated = demo.submit(_next_event(), EDITOR)
    assert updated["id"] == first["id"] and updated["revision"] == first["revision"] + 1
    assert updated.get("profile") == profile
    with pytest.raises(WorkbenchError, match="case_not_found"):
        demo.get_case(updated["id"], guest)
    assert demo.cases(guest) == []


def test_authenticated_chat_escalations_do_not_expose_linked_or_standalone_evidence(tmp_path, monkeypatch):
    from types import SimpleNamespace

    engine = Workbench(tmp_path / "demo.sqlite3")
    guest = Actor("demo-guest", frozenset({"admin"}), authenticated=False)
    case = review(engine)
    linked = engine.chat(EDITOR, agent="governance", message="Discuss confidential strategy", case_id=case["id"])
    standalone = engine.chat(EDITOR, agent="governance", message="Confidential standalone question")
    legacy = engine.chat(guest, agent="governance", message="Public synthetic question")
    for escalation in (linked, standalone):
        with pytest.raises(WorkbenchError, match="case_not_found"):
            engine.get_case(escalation["case_id"], guest)
    assert f"Related case: {case['id']}" in engine.get_case(linked["case_id"], APPROVER)["evidence"]
    assert [item["id"] for item in engine.cases(guest)] == [legacy["case_id"]]

    with engine.store.transaction(write=False) as connection:
        legacy_record = engine.store.load_case(connection, legacy["case_id"])
        private_record = engine.store.load_case(connection, linked["case_id"])
    with monkeypatch.context() as patch:
        patch.setattr("ai_factory_workbench.service.uuid.uuid4", lambda: SimpleNamespace(hex=legacy_record["subject"]))
        with pytest.raises(WorkbenchError, match="review_subject_conflict"):
            engine.chat(EDITOR, agent="governance", message="Unshared confidential question")
    with monkeypatch.context() as patch:
        patch.setattr("ai_factory_workbench.service.uuid.uuid4", lambda: SimpleNamespace(hex=private_record["subject"]))
        with pytest.raises(WorkbenchError, match="case_not_found"):
            engine.chat(guest, agent="governance", message="Attempt to reuse a private subject")
    assert engine.get_case(legacy["case_id"], guest)["reason"] == "Public synthetic question"
