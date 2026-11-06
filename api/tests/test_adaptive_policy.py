"""Acceptance tests for data-only review profiles and attested approvals."""

from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from ai_factory_workbench import Actor, Workbench, WorkbenchError
from ai_factory_workbench.approvals import ApprovalRelay, RelayAuthority, sign_approval
from ai_factory_workbench.profiles import WorkProfile, compare_policies, get_profile, list_profiles

NOW = dt.datetime(2026, 11, 6, 10, 0, tzinfo=dt.timezone.utc)
SLACK_KEY = bytes(range(32))
TEAMS_KEY = bytes(range(32, 64))
SERVICE_KEY = bytes(range(64, 96))
SUBMITTER = Actor("maya", frozenset({"admin"}), subject="workspace:maya")
APPROVER = Actor("noor", frozenset({"approver"}), subject="workspace:noor")
PROVIDER_ALIAS = Actor("maya-provider-alias", frozenset({"approver"}), subject="workspace:maya")
MACHINE = Actor("internal-approvals", frozenset({"approver"}))
VIEWER = Actor("observer", frozenset({"viewer"}))
EVIDENCE = {
    "artifact_ref": "model/v2",
    "evaluation_ref": "eval/v2",
    "risk_summary": "Review the validation errors.",
    "rollback_plan": "Restore model/v1.",
}


@pytest.fixture
def engine(tmp_path):
    return Workbench(tmp_path / "governance.sqlite3", mode="protected_demo", clock=lambda: NOW)


def _review(engine, subject="release/v2"):
    return get_profile().prepare(engine, SUBMITTER, subject=subject, title="Release model v2",
                                 reason="Review release evidence.", evidence=EVIDENCE)


def _authorities():
    return (
        RelayAuthority("slack-ci", SLACK_KEY, "slack", {
            "U-NOOR": APPROVER, "U-MAYA": PROVIDER_ALIAS, "U-VIEWER": VIEWER,
            "U-ANON": replace(APPROVER, authenticated=False),
        }),
        RelayAuthority("teams-ci", TEAMS_KEY, "teams", {"tenant:noor": APPROVER}),
        RelayAuthority("approval-ci", SERVICE_KEY, "service", {"employee:noor": APPROVER, "bot": MACHINE},
                       frozenset({"bot"})),
    )


def _envelope(engine, case, **changes):
    now = int(NOW.timestamp())
    return {
        "workspace": engine.workspace, "issuer": "slack-ci", "channel": "slack", "identity": "U-NOOR",
        "case_id": case["id"], "revision": case["revision"], "proposal_hash": case["proposal_hash"],
        "decision": "approve", "rationale": "Revue indépendante du risque et du plan de retour.",
        "issued_at": now, "expires_at": now + 60, "request_id": "request-1",
        **changes,
    }


def _approve(engine, actor, case):
    return engine.decide(case["id"], actor, decision="approve", revision=case["revision"],
                         proposal_hash=case["proposal_hash"], rationale="Reviewed the evidence.")


def test_profile_validates_evidence_and_freezes_the_method_in_a_review(engine):
    profile = get_profile()
    assert [entry.id for entry in list_profiles()] == ["governance", "operations", "platform"]
    assert WorkProfile.from_dict(profile.describe()) == profile

    with pytest.raises(WorkbenchError, match="profile_evidence_required"):
        profile.prepare(engine, SUBMITTER, subject="release", title="Title", reason="Reason",
                        evidence={"artifact_ref": "model/v2"})
    with pytest.raises(WorkbenchError, match="invalid_profile_evidence"):
        profile.prepare(engine, SUBMITTER, subject="release", title="Title", reason="Reason",
                        evidence={**EVIDENCE, "artifact_ref": " "})
    with pytest.raises(WorkbenchError, match="forbidden"):
        profile.prepare(engine, VIEWER, subject="release", title="Title", reason="Reason", evidence=EVIDENCE)
    assert engine.cases(SUBMITTER) == []

    case = _review(engine)
    assert case["profile"] == profile.snapshot()
    assert case["kind"] == "strategic" and case["action"] is None
    assert "rollback_plan: Restore model/v1." in case["evidence"]
    assert _review(engine)["proposal_hash"] == case["proposal_hash"]

    candidate = replace(profile, version="2", review_steps=(*profile.review_steps, "Check monitoring."))
    revised = candidate.prepare(engine, SUBMITTER, subject="release/v2", title="Release model v2",
                                reason="Review release evidence.", evidence=EVIDENCE)
    assert revised["revision"] == case["revision"] + 1
    assert revised["proposal_hash"] != case["proposal_hash"]
    with pytest.raises(WorkbenchError, match="stale_proposal"):
        _approve(engine, APPROVER, case)
    assert _approve(engine, APPROVER, revised)["status"] == "decided"


def test_profile_rejects_executable_fields_and_candidate_replay_is_read_only(engine):
    baseline, candidate = get_profile(), get_profile("operations")
    with pytest.raises(WorkbenchError, match="invalid_work_profile"):
        WorkProfile.from_dict({**baseline.describe(), "script": "run arbitrary code"})
    with pytest.raises(WorkbenchError, match="invalid_work_profile"):
        replace(baseline, allow_service_drafts=True)
    with pytest.raises(WorkbenchError, match="invalid_work_profile"):
        replace(candidate, approval_channels=("service",))
    with pytest.raises(WorkbenchError, match="work_profile_not_found"):
        get_profile("../untrusted.py")

    before = baseline.snapshot()
    report = compare_policies(baseline, candidate)
    assert report == compare_policies(baseline, candidate)
    assert report["sample"] == "synthetic" and report["cases_examined"] == 2 and report["changed_count"] == 2
    assert report["changes"][1]["baseline"]["channel_disposition"] == "human_required"
    assert report["changes"][1]["candidate"]["ready_for_review"]
    assert report["rule_changes"]["allow_service_drafts"] == {"baseline": False, "candidate": True}
    assert baseline.snapshot() == before and engine.cases(SUBMITTER) == []


@pytest.mark.parametrize(("issuer", "channel", "identity", "key"), [
    ("slack-ci", "slack", "U-NOOR", SLACK_KEY),
    ("teams-ci", "teams", "tenant:noor", TEAMS_KEY),
    ("approval-ci", "service", "employee:noor", SERVICE_KEY),
])
def test_approval_channels_share_exact_decision_and_persist_attestation(
    engine, issuer, channel, identity, key,
):
    case = _review(engine)
    relay = ApprovalRelay(engine, get_profile(), _authorities())
    envelope = _envelope(engine, case, issuer=issuer, channel=channel, identity=identity)
    signature = sign_approval(key, envelope)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: relay.decide(envelope, signature), range(2)))
    assert all(result["status"] == "decided" for result in results)
    decisions = engine.history(case["id"], APPROVER)["decisions"]
    assert len(decisions) == 1 and decisions[0]["actor"] == APPROVER.actor
    proof = decisions[0]["provenance"]
    assert proof["issuer"] == issuer and proof["channel"] == channel and proof["identity"] == identity
    assert proof["request_id"] == envelope["request_id"] and len(proof["proof_sha256"]) == 64
    assert signature not in str(decisions) and key.hex() not in str(decisions)
    assert "provenance" not in engine.history(case["id"], VIEWER)["decisions"][0]


def test_signature_scope_expiry_and_identity_fail_closed(engine):
    case = _review(engine)
    relay = ApprovalRelay(engine, get_profile(), _authorities())
    envelope = _envelope(engine, case)
    signature = sign_approval(SLACK_KEY, envelope)

    with pytest.raises(WorkbenchError, match="approval_signature_invalid"):
        relay.decide({**envelope, "rationale": "Tampered rationale."}, signature)
    with pytest.raises(WorkbenchError, match="approval_signature_invalid"):
        relay.decide({**envelope, "issuer": "attacker"}, signature)
    with pytest.raises(WorkbenchError, match="invalid_approval_envelope"):
        relay.decide({**envelope, "revision": True}, signature)
    with pytest.raises(WorkbenchError, match="invalid_approval_envelope"):
        relay.decide({**envelope, "actor": "admin"}, signature)
    for updates in ({"workspace": "other-company"}, {"channel": "teams"}):
        bad_scope = {**envelope, **updates}
        with pytest.raises(WorkbenchError, match="approval_scope_mismatch"):
            relay.decide(bad_scope, sign_approval(SLACK_KEY, bad_scope))

    for identity in ("unknown", "U-VIEWER", "U-ANON"):
        bad_identity = {**envelope, "identity": identity}
        with pytest.raises(WorkbenchError, match="approval_identity_forbidden"):
            relay.decide(bad_identity, sign_approval(SLACK_KEY, bad_identity))

    alias = {**envelope, "identity": "U-MAYA"}
    with pytest.raises(WorkbenchError, match="self_approval_forbidden"):
        relay.decide(alias, sign_approval(SLACK_KEY, alias))
    later = ApprovalRelay(engine, get_profile(), _authorities(), clock=lambda: NOW + dt.timedelta(seconds=60))
    with pytest.raises(WorkbenchError, match="approval_attestation_expired"):
        later.decide(envelope, signature)
    assert engine.history(case["id"], APPROVER)["decisions"] == []


def test_replay_request_id_cannot_be_rebound_to_another_case_after_restart(engine):
    first, second = _review(engine, "release/first"), _review(engine, "release/second")
    initial = ApprovalRelay(engine, get_profile(), _authorities())
    envelope = _envelope(engine, first)
    signature = sign_approval(SLACK_KEY, envelope)
    initial.decide(envelope, signature)

    reopened = Workbench(engine.store.path, mode=engine.mode, clock=engine.clock, workspace=engine.workspace)
    relay = ApprovalRelay(reopened, get_profile(), _authorities())
    assert relay.decide(envelope, signature)["status"] == "decided"
    conflicting = _envelope(engine, second)
    with pytest.raises(WorkbenchError) as caught:
        relay.decide(conflicting, sign_approval(SLACK_KEY, conflicting))
    assert caught.value.status == 409
    assert reopened.history(second["id"], APPROVER)["decisions"] == []


def test_service_requires_human_for_governance_and_profile_mismatch_fails(engine):
    case = _review(engine)
    human = _envelope(engine, case, issuer="approval-ci", channel="service", identity="employee:noor")
    machine = {**human, "identity": "bot"}
    governance = ApprovalRelay(engine, get_profile(), _authorities())
    with pytest.raises(WorkbenchError, match="human_approval_required"):
        governance.decide(machine, sign_approval(SERVICE_KEY, machine))
    operations = ApprovalRelay(engine, get_profile("operations"), _authorities())
    with pytest.raises(WorkbenchError, match="approval_profile_mismatch"):
        operations.decide(human, sign_approval(SERVICE_KEY, human))
    assert engine.history(case["id"], APPROVER)["decisions"] == []


def test_direct_internal_service_can_only_approve_allowlisted_erp_drafts(engine):
    event = {
        "specversion": "1.0", "source": "crm-sim", "id": "delivery-1", "type": "crm.opportunity.won",
        "subject": "opp-real-test",
        "data": {"title": "Atelier Acme renewal", "customer_name": "Atelier Acme", "customer_ref": "CRM-ACME",
                 "amount_minor": 128900, "currency": "EUR", "record_version": 1},
    }
    governance = ApprovalRelay(engine, get_profile(), _authorities())
    operations_profile = get_profile("operations")
    operations = ApprovalRelay(engine, operations_profile, _authorities())

    legacy = engine.submit(event, SUBMITTER)
    envelope = _envelope(engine, legacy, issuer="approval-ci", channel="service", identity="bot")
    with pytest.raises(WorkbenchError, match="human_approval_required"):
        operations.decide(envelope, sign_approval(SERVICE_KEY, envelope))

    v2 = {**event, "id": "delivery-2", "data": {**event["data"], "record_version": 2}}
    case = engine.submit(v2, SUBMITTER, profile=get_profile().snapshot())
    envelope = _envelope(engine, case, issuer="approval-ci", channel="service", identity="bot")
    signature = sign_approval(SERVICE_KEY, envelope)
    with pytest.raises(WorkbenchError, match="human_approval_required"):
        governance.decide(envelope, signature)
    with pytest.raises(WorkbenchError, match="approval_profile_mismatch"):
        operations.decide(envelope, signature)
    assert engine.submit(v2, SUBMITTER, profile=operations_profile.snapshot())["proposal_hash"] == case["proposal_hash"]
    assert engine.store.draft_count() == 0

    v3 = {**event, "id": "delivery-3", "data": {**event["data"], "record_version": 3}}
    case = engine.submit(v3, SUBMITTER, profile=operations_profile.snapshot())
    envelope = _envelope(engine, case, issuer="approval-ci", channel="service", identity="bot")
    assert operations.decide(envelope, sign_approval(SERVICE_KEY, envelope))["status"] == "executed"
    assert engine.store.draft_count() == 1


def test_relay_rejects_human_alias_for_machine_account(engine):
    slack, _, service = _authorities()
    machine = replace(MACHINE, username="Operator@Example.Test")
    authority = replace(service, actors={**service.actors, "bot": machine})
    relay = ApprovalRelay(engine, get_profile(), (slack, authority))
    assert {"internal-approvals", "operator@example.test"} <= relay.machine_actor_ids
    spoofed_human = replace(APPROVER, subject="operator@example.test")
    with pytest.raises(ValueError, match="human and service approval identities must be separate"):
        ApprovalRelay(engine, get_profile(), (replace(slack, actors={"U-NOOR": spoofed_human}), authority))


def test_relay_requires_distinct_issuer_keys_and_cannot_expose_secrets(engine):
    slack, _, _ = _authorities()
    assert repr(SLACK_KEY) not in repr(slack)
    with pytest.raises(ValueError, match="unique issuers and keys"):
        ApprovalRelay(engine, get_profile(), (slack, replace(slack, issuer="slack-other")))
    with pytest.raises(ValueError, match="invalid approval relay authority"):
        replace(slack, service_identities=frozenset({"U-NOOR"}))
