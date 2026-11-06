from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from ai_factory_workbench import Actor, AdapterCapabilities, DraftReadback, Workbench, WorkbenchError
from ai_factory_workbench._hashing import digest
from ai_factory_workbench.crm import CRMOpportunity, sync_opportunities
from ai_factory_workbench.cyberlog import JournalError, iter_verified_events, journal_request, set_journal_enabled


NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
EDITOR = Actor("editor-private@example.test", frozenset({"editor"}))
APPROVER = Actor("approver-private@example.test", frozenset({"approver"}))
SOURCE = "salesforce/cyber"
EVENT = {
    "specversion": "1.0", "type": "crm.opportunity.won", "source": "crm-sim",
    "id": "journal-event", "subject": "journal-opportunity",
    "data": {"title": "Confidential order title", "customer_name": "Atelier Acme",
             "customer_ref": "CRM-ACME", "amount_minor": 2300, "currency": "EUR", "record_version": 1},
}


def review(engine, subject):
    return engine.propose_review(EDITOR, subject=subject, title="Confidential title", kind="strategic",
                                 reason="Confidential reason", evidence=["Confidential evidence"])


def approve(engine, case, **kwargs):
    return engine.decide(case["id"], APPROVER, revision=case["revision"], proposal_hash=case["proposal_hash"],
                         decision="approve", rationale="Confidential rationale\r\nforged-log-row", **kwargs)


def labels(path):
    return [event["event"] for event in iter_verified_events(path)]


def test_default_off_explicit_policy_and_shared_store(tmp_path):
    path = tmp_path / "workbench #.sqlite3"
    default = Workbench(path)
    review(default, "before-enabled")
    assert labels(path) == []

    assert set_journal_enabled(path, True)
    assert not set_journal_enabled(path, True)
    review(default, "writer-open-before-enabled")
    review(Workbench(path), "other-process-default")
    assert labels(path) == ["journal.enabled", "case.proposed", "case.proposed"]

    assert set_journal_enabled(path, False)
    assert not set_journal_enabled(path, False)
    review(default, "writer-open-before-disabled")
    review(Workbench(path, cyber_audit=True), "true-cannot-silently-reenable")
    assert labels(path)[-1] == "journal.disabled"
    assert len(labels(path)) == 4

    assert set_journal_enabled(path, True)
    review(default, "resumed")
    assert labels(path)[-2:] == ["journal.enabled", "case.proposed"]


def test_bound_decision_uses_pseudonyms_and_exact_revision_without_payload(tmp_path):
    path = tmp_path / "workbench.sqlite3"
    engine = Workbench(path, cyber_audit=True, clock=lambda: NOW)
    with journal_request("internal request/private"):
        case = engine.submit(EVENT, EDITOR)
    proof = {
        "issuer": "private-issuer", "channel": "slack", "identity": "private-slack-identity",
        "request_id": "private-slack-request", "proof_sha256": "a" * 64,
        "expires_at": (NOW + timedelta(minutes=1)).isoformat(),
    }
    assert approve(engine, case, provenance=proof)["status"] == "executed"
    assert approve(engine, case, provenance=proof)["status"] == "executed"

    events = list(iter_verified_events(path))
    assert [event["event"] for event in events] == ["journal.enabled", "case.proposed", "decision.recorded",
                                                   "execution.claimed", "execution.dispatch_authorized",
                                                   "execution.completed"]
    assert len({event["case_ref"] for event in events[1:]}) == 1
    assert all(event["proposal_hash"] == case["proposal_hash"] and event["revision"] == case["revision"]
               for event in events[1:])
    assert events[2]["outcome"] == "approved" and events[2]["channel"] == "slack"
    assert events[2]["actor_ref"] == digest(["workbench-cyber-ref-v1", "demo", "actor", APPROVER.actor])
    assert events[2]["actor_ref"] == events[4]["actor_ref"] == events[5]["actor_ref"] != events[1]["actor_ref"]
    assert len(events[1]["request_id"]) == len(events[2]["request_id"]) == 64
    assert "request_id" not in events[3]
    serialized = json.dumps(events)
    for secret in ("Confidential", "private", "forged-log-row", "CRM-ACME", "journal-opportunity", case["id"]):
        assert secret not in serialized


def test_concurrent_writers_and_reader_snapshot(tmp_path):
    path = tmp_path / "workbench.sqlite3"
    first = Workbench(path, cyber_audit=True)
    first.cases(APPROVER)
    second = Workbench(path)

    def propose(index):
        engine = first if index % 2 == 0 else second
        with journal_request(f"request-{index}"):
            review(engine, f"subject-{index}")

    snapshot = iter_verified_events(path)
    assert next(snapshot)["event"] == "journal.enabled"
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(propose, range(16)))
    assert list(snapshot) == []
    events = list(iter_verified_events(path))
    assert [event["seq"] for event in events] == list(range(1, 18))
    assert len({event["request_id"] for event in events[1:]}) == 16


@pytest.mark.parametrize("event", ["decision.recorded", "execution.completed"])
def test_journal_failure_rolls_back_sensitive_state_and_retains_recoverability(tmp_path, event):
    path = tmp_path / "workbench.sqlite3"
    engine = Workbench(path, cyber_audit=True, clock=lambda: NOW)
    case = engine.submit(EVENT, EDITOR)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"""CREATE TRIGGER reject_test_event BEFORE INSERT ON cyber_events
                              WHEN NEW.event = '{event}'
                              BEGIN SELECT RAISE(ABORT, 'private failure payload'); END""")
    with pytest.raises(WorkbenchError, match="^cyber_journal_unavailable$") as error:
        approve(engine, case)
    assert error.value.status == 503
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DROP TRIGGER reject_test_event")
    if event == "decision.recorded":
        assert engine.get_case(case["id"], APPROVER)["status"] == "waiting_review"
        assert labels(path) == ["journal.enabled", "case.proposed"]
        assert engine.store.draft_count() == 0
    else:
        assert engine.get_case(case["id"], APPROVER)["status"] == "applying"
        assert "execution.completed" not in labels(path)
        assert engine.store.draft_count() == 1
    assert approve(engine, case)["status"] == "executed"
    assert engine.store.draft_count() == labels(path).count("execution.completed") == 1


@pytest.mark.parametrize("journal_fails", [False, True])
def test_lost_erp_response_then_retry_preserves_uncertainty_and_one_completion(tmp_path, monkeypatch, journal_fails):
    path = tmp_path / "workbench.sqlite3"
    engine = Workbench(path, cyber_audit=True, clock=lambda: NOW)
    case = engine.submit(EVENT, EDITOR)
    original = engine.store.create_draft

    def lost_response(*args, **kwargs):
        original(*args, **kwargs)
        raise TimeoutError("private ERP response")

    if journal_fails:
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("""CREATE TRIGGER reject_unknown BEFORE INSERT ON cyber_events
                                  WHEN NEW.event = 'execution.unknown'
                                  BEGIN SELECT RAISE(ABORT, 'private audit failure'); END""")
    monkeypatch.setattr(engine.store, "create_draft", lost_response)
    with pytest.raises(WorkbenchError, match="^outcome_unknown_retry_same_approval$"):
        approve(engine, case)
    expected = ["journal.enabled", "case.proposed", "decision.recorded", "execution.claimed",
                "execution.dispatch_authorized"]
    if not journal_fails:
        expected.append("execution.unknown")
    assert labels(path) == expected
    reopened = Workbench(path, clock=lambda: NOW)
    assert approve(reopened, case)["status"] == "executed"
    assert reopened.store.draft_count() == 1
    assert labels(path) == expected + ["execution.completed"]


@pytest.mark.parametrize("external", [False, True])
def test_claimed_retry_requires_a_durable_dispatch_but_readback_does_not(tmp_path, monkeypatch, external):
    path = tmp_path / "workbench.sqlite3"

    class ERP:
        target = "audited-erp"
        capabilities = AdapterCapabilities(draft_writes=True, idempotency_keys=True)

        def __init__(self):
            self.drafts = {}
            self.read_times_out = True

        def ensure_draft(self, external_ref, fields):
            assert external_ref not in self.drafts
            assert labels(path)[-1] == "execution.dispatch_authorized"
            self.drafts[external_ref] = DraftReadback("external-draft", external_ref, fields)

        def read_draft(self, external_ref):
            if self.read_times_out:
                self.read_times_out = False
                raise TimeoutError
            return self.drafts.get(external_ref)

    connector = ERP() if external else None
    source = "explicit-crm" if external else EVENT["source"]
    engine = Workbench(path, connector=connector, crm_source=source, cyber_audit=True, defer_execution=True,
                       clock=lambda: NOW, customers=[{"id": "erp-acme", "tenant": "demo", "name": "Atelier Acme",
                                                       "reference": "CRM-ACME"}])
    case = engine.submit({**EVENT, "source": source}, EDITOR)
    assert approve(engine, case)["status"] == "applying"

    def timeout_before_local_dispatch(*args, **kwargs):
        raise TimeoutError

    if not external:
        with monkeypatch.context() as m:
            m.setattr(engine.store, "create_draft", timeout_before_local_dispatch)
            with pytest.raises(WorkbenchError, match="outcome_unknown_retry_same_approval"):
                engine.reconcile_pending(case["id"])
    else:
        with pytest.raises(WorkbenchError, match="outcome_unknown_retry_same_approval"):
            engine.reconcile_pending(case["id"])

    def set_failure(trigger):
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("DROP TRIGGER IF EXISTS reject_audit")
            connection.execute(trigger)

    reject_dispatch = """CREATE TRIGGER reject_audit BEFORE INSERT ON cyber_events
                         WHEN NEW.event = 'execution.dispatch_authorized'
                         BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END"""
    set_failure(reject_dispatch)
    with pytest.raises(WorkbenchError, match="^cyber_journal_unavailable$"):
        engine.reconcile_pending(case["id"])
    assert (len(connector.drafts) if external else engine.store.draft_count()) == 0
    assert "execution.dispatch_authorized" not in labels(path)

    set_failure("""CREATE TRIGGER reject_audit BEFORE INSERT ON cyber_events
                   WHEN NEW.event = 'execution.completed'
                   BEGIN SELECT RAISE(ABORT, 'receipt audit unavailable'); END""")
    with pytest.raises(WorkbenchError, match="^cyber_journal_unavailable$"):
        engine.reconcile_pending(case["id"])
    assert (len(connector.drafts) if external else engine.store.draft_count()) == 1

    set_failure(reject_dispatch)
    assert engine.reconcile_pending(case["id"])["status"] == "executed"
    assert (len(connector.drafts) if external else engine.store.draft_count()) == 1
    assert labels(path).count("execution.dispatch_authorized") == 1
    assert labels(path).count("execution.completed") == 1


def test_direct_sandbox_write_cannot_bypass_enabled_journal(tmp_path):
    path = tmp_path / "workbench.sqlite3"
    engine = Workbench(path, cyber_audit=True)
    with pytest.raises(WorkbenchError, match="^cyber_journal_unavailable$"):
        engine.store.create_draft("unbound-operation", {"amount": 1})
    assert engine.store.draft_count() == 0
    assert labels(path) == ["journal.enabled"]


def test_definitive_erp_conflict_logs_hold_once(tmp_path, monkeypatch):
    path = tmp_path / "workbench.sqlite3"
    engine = Workbench(path, cyber_audit=True, clock=lambda: NOW)
    case = engine.submit(EVENT, EDITOR)

    def conflict(*args, **kwargs):
        raise WorkbenchError("draft_conflict", 409)

    monkeypatch.setattr(engine.store, "create_draft", conflict)
    with pytest.raises(WorkbenchError, match="^draft_conflict$"):
        approve(engine, case)
    with pytest.raises(WorkbenchError, match="^reconciliation_required$"):
        approve(engine, case)
    holds = [event for event in iter_verified_events(path) if event["event"] == "execution.hold"]
    assert len(holds) == 1 and holds[0]["reason_code"] == "erp_readback_conflict"


def test_crm_checkpoint_replay_and_withdrawal(tmp_path):
    path = tmp_path / "workbench.sqlite3"
    engine = Workbench(path, cyber_audit=True, crm_sources=(SOURCE,), clock=lambda: NOW,
                       customers=[{"id": "erp-acme", "tenant": "demo", "name": "Acme",
                                   "reference": f"{SOURCE}/account-private"}])

    class Feed:
        source = SOURCE

        def opportunities(self, start, end):
            yield self.row

    feed = Feed()
    feed.row = CRMOpportunity("private-deal", NOW + timedelta(seconds=1), True, "Private CRM title",
                              "account-private", "Acme", "123.45", "EUR")
    end = NOW + timedelta(seconds=2)
    sync_opportunities(engine, feed, EDITOR, since=NOW, until=end)
    sync_opportunities(engine, feed, EDITOR, until=end)
    feed.row = CRMOpportunity("private-deal", NOW + timedelta(seconds=3), False)
    sync_opportunities(engine, feed, EDITOR, until=NOW + timedelta(seconds=4))
    assert labels(path) == ["journal.enabled", "case.proposed", "crm.checkpoint", "case.withdrawn", "crm.checkpoint"]
    events = list(iter_verified_events(path))
    assert len({event["source_ref"] for event in events[1:]}) == 1
    assert "private-deal" not in json.dumps(events)


def test_append_guards_and_offline_tamper_detection(tmp_path):
    path = tmp_path / "workbench.sqlite3"
    review(Workbench(path, cyber_audit=True), "case")
    with closing(sqlite3.connect(path)) as connection, connection:
        for statement in ("UPDATE cyber_events SET hash='forged' WHERE seq=2", "DELETE FROM cyber_events WHERE seq=2",
                          "INSERT OR REPLACE INTO cyber_events SELECT * FROM cyber_events WHERE seq=2"):
            with pytest.raises(sqlite3.IntegrityError, match="cyber journal"):
                connection.execute(statement)
        connection.execute("DROP TRIGGER cyber_events_no_update")
        connection.execute("UPDATE cyber_events SET record = '{} ' WHERE seq=2")
    with pytest.raises(JournalError, match="invalid journal event"):
        list(iter_verified_events(path))


def test_offline_reader_and_policy_refuse_to_create_or_adopt_database(tmp_path):
    path = tmp_path / "not-workbench.sqlite3"
    with pytest.raises(JournalError):
        list(iter_verified_events(path))
    with pytest.raises(JournalError):
        set_journal_enabled(path, True)
    assert not path.exists()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
    with pytest.raises(JournalError, match="not a bound Workbench database"):
        set_journal_enabled(path, True)
    with pytest.raises(JournalError, match="not a bound Workbench database"):
        list(iter_verified_events(path))
