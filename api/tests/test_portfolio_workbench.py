"""Durable, private Workbench decisions for exact Portfolio handoffs."""

from dataclasses import replace

import pytest

from ai_factory_workbench import Actor, Workbench, WorkbenchError
from ai_factory_workbench.cyberlog import iter_verified_events, set_journal_enabled
from app.portfolio_workbench import reconcile_portfolio_reviews
from app.settings import Settings
from tests.test_bu_access import CONFIG, ROLE_MAP, policy_file
from tests.test_portfolio import (
    AUTONOMY, BINDINGS, GLOBAL, GROUPS, OPS_LEAD, PORTFOLIO_ROLES, REL, REL_AGENT, REL_ARCHITECT, REL_MANAGER,
    assign, command, post, project, snapshot, task,
)
from tests.test_security import proxy, who

WORKBENCH = "/api/v1/workbench"
REVIEWER = who("independent", "portfolio-approver")


def client(tmp_path, mode="portfolio"):
    groups = {**GROUPS, "portfolio-approver": ["*"]}
    path = policy_file(tmp_path, groups=groups, portfolio_roles=PORTFOLIO_ROLES,
                       portfolio_agents=BINDINGS, portfolio_autonomy=AUTONOMY)
    roles = ROLE_MAP + ("finance-admin:admin", "portfolio-approver:approver") + tuple(
        f"{group}:viewer" for group in GROUPS if group.startswith(("ops-", "relation-"))
    )
    return proxy(tmp_path, config_dir=str(CONFIG), role_map=roles, bu_policy_path=path,
                 demo_mode="never", workbench_mode=mode,
                 workbench_cyber_audit="on" if mode == "portfolio" else "off")


def submit_sensitive(api, *, technical=False):
    row = task(api, 3, project(api, unit=REL), REL, execution="sensitive", technical=technical,
               description="Evaluate the operational change against the approved procedure.")
    row = assign(api, row, REL_MANAGER, "agent", "ag-support-supervisor")
    row = command(api, row, REL_AGENT, "start")
    row = command(api, row, REL_AGENT, "submit", evidence=[
        {"kind": "work", "label": "Assessment report", "ref": "REPORT-01"},
    ])
    if technical:
        assert row["status"] == "review"
        row = command(api, row, REL_ARCHITECT, "approve")
    assert row["status"] == "workbench"
    return row


def queue(api, identity=REVIEWER):
    response = api.get(f"{WORKBENCH}/cases", headers=identity)
    assert response.status_code == 200, response.text
    return response.json()


def decision(api, case, outcome="approve", *, identity=REVIEWER, status=200, **patch):
    response = api.post(f"{WORKBENCH}/cases/{case['id']}/decisions", headers=identity, json={
        "decision": outcome, "revision": case["revision"], "proposal_hash": case["proposal_hash"],
        "rationale": "Private independent approval rationale.", **patch,
    })
    assert response.status_code == status, response.text
    return response.json()


def test_handoff_preserves_bu_scope_and_four_eyes(tmp_path):
    api = client(tmp_path)
    assert queue(api) == {"mode": "portfolio", "cases": [], "capabilities": {
        "can_submit": False, "can_decide": True, "chat_source": "disabled",
    }}
    assert not api.app.state.workbench.store.path.exists()
    assert api.get(f"{WORKBENCH}/encounters", headers=REVIEWER).json() == {"encounters": []}
    assert not api.app.state.workbench.store.path.exists()
    row = submit_sensitive(api, technical=True)
    assert queue(api)["cases"] == []
    assert reconcile_portfolio_reviews(api.app) == 0
    [case] = queue(api)["cases"]
    assert api.get(f"{WORKBENCH}/encounters", headers=REVIEWER).json() == {"encounters": [
        {"case_id": case["id"], "revision": case["revision"], "status": "waiting"},
    ]}
    assert case["action"] is None and case["kind"] == "strategic" and case["can_decide"] is True
    assert case["evidence"] == ["work · Assessment report · REPORT-01"]
    assert api.get(f"{WORKBENCH}/cases", headers=REL_ARCHITECT).status_code == 403
    assert api.get(f"{WORKBENCH}/cases", headers={**REL_AGENT, "X-Forwarded-Groups": "global-admin"}).status_code == 403
    mixed = who("mixed", "global-read", "finance-admin")
    assert queue(api, mixed)["capabilities"]["can_decide"] is False
    assert decision(api, case, identity=mixed, status=403)["detail"] == "forbidden"
    architect_alias = who("architect-alias", "portfolio-approver", email=REL_ARCHITECT["X-Forwarded-Email"])
    for outcome in ("approve", "reject", "request_changes"):
        assert decision(api, case, outcome, identity=architect_alias, status=403)["detail"] == "self_approval_forbidden"
    assert decision(api, case, status=409, revision=2)["detail"] == "stale_proposal"
    assert decision(api, case, status=409, proposal_hash="f" * 64)["detail"] == "stale_proposal"
    assert decision(api, case)["status"] == "decided"
    assert snapshot(api, REL_MANAGER)["tasks"][0]["status"] == "workbench"
    assert reconcile_portfolio_reviews(api.app) == 1
    assert reconcile_portfolio_reviews(api.app) == 0
    [result] = snapshot(api, REL_MANAGER)["tasks"]
    assert result["revision"] == row["revision"] + 1 and result["status"] == "done"
    assert result["workbench_decision"]["outcome"] == "approve"
    assert result["history"][0]["action"] == "workbench_approve"
    assert result["history"][0]["actor"] == "Workbench"
    assert set(result["workbench_decision"]) == {"outcome", "at"}
    assert "independent" not in str(result) and case["id"] not in str(result)
    assert snapshot(api, OPS_LEAD)["tasks"] == []


def test_recovery_requires_a_single_global_admin_grant(tmp_path):
    api = client(tmp_path, mode="protected_demo")
    route = f"{WORKBENCH}/recovery"
    assert api.get(route, headers=REVIEWER).status_code == 403
    assert api.get(route, headers=who("mixed", "portfolio-approver", "finance-admin")).status_code == 403
    assert api.get(route, headers=GLOBAL).json() == {"cases": [], "next_after": None}


def test_restart_between_case_creation_binding_and_decision(tmp_path, monkeypatch):
    api = client(tmp_path)
    row = submit_sensitive(api)
    store = api.app.state.portfolio

    def fail_bind(*_):
        raise OSError("simulated process loss")

    monkeypatch.setattr(store, "bind_workbench_review", fail_bind)
    assert reconcile_portfolio_reviews(api.app) == 0
    [case] = queue(api)["cases"]
    assert decision(api, case, "reject")["status"] == "rejected"
    assert snapshot(api, REL_MANAGER)["tasks"][0]["status"] == "workbench"
    recovered = client(tmp_path)
    assert reconcile_portfolio_reviews(recovered.app) == 1
    [result] = snapshot(recovered, REL_MANAGER)["tasks"]
    assert result["status"] == "cancelled" and result["revision"] == row["revision"] + 1
    assert [item["id"] for item in queue(recovered)["cases"]] == [case["id"]]
    assert reconcile_portfolio_reviews(recovered.app) == 0


def test_rework_creates_new_case_and_rejects_old_or_unbound_receipt(tmp_path):
    api = client(tmp_path)
    row = submit_sensitive(api)
    assert reconcile_portfolio_reviews(api.app) == 0
    [case] = queue(api)["cases"]
    [binding] = api.app.state.portfolio.pending_workbench_reviews(linked=True, after=0, limit=10)
    assert command(api, row, REL_MANAGER, "workbench_approve", status=422)["detail"] == "invalid_portfolio_request"
    assert decision(api, case, "request_changes")["status"] == "changes_requested"
    wrong = replace(binding, proposal_hash="f" * 64)
    assert not api.app.state.portfolio.apply_workbench_receipt(wrong, "approve", "2026-11-03T10:00:00+00:00")
    assert api.app.state.workbench._portfolio_receipt(binding.subject, case["id"], 1, wrong.proposal_hash) is None
    assert reconcile_portfolio_reviews(api.app) == 1
    [ready] = snapshot(api, REL_AGENT)["tasks"]
    assert ready["status"] == "ready" and ready["evidence"] == [] and ready["start_authorized"] is False
    fresh = command(api, ready, REL_AGENT, "start")
    fresh = command(api, fresh, REL_AGENT, "submit")
    assert fresh["status"] == "workbench"
    assert reconcile_portfolio_reviews(api.app) == 0
    new_case = next(item for item in queue(api)["cases"] if item["id"] != case["id"])
    assert new_case["status"] == "waiting_review"
    assert decision(api, case, "request_changes")["status"] == "changes_requested"
    assert reconcile_portfolio_reviews(api.app) == 0
    assert snapshot(api, REL_MANAGER)["tasks"][0]["status"] == "workbench"
    assert decision(api, new_case)["status"] == "decided"
    assert reconcile_portfolio_reviews(api.app) == 1
    assert snapshot(api, REL_MANAGER)["tasks"][0]["status"] == "done"


def test_production_mode_rejects_foreign_cases_and_sandbox_surfaces(tmp_path):
    api = client(tmp_path)
    engine = api.app.state.workbench
    generic = Workbench(engine.store.path, mode="protected_demo", workspace=engine.workspace)
    actor = Actor("generic-admin", frozenset({"admin"}), authenticated=True)
    subject = f"portfolio/{'1' * 32}/1"
    case = generic.propose_review(actor, subject=subject, title="Unrelated review", kind="strategic",
                                  reason="Do not accept generic content.", evidence=[])
    assert queue(api)["cases"] == []
    assert api.get(f"{WORKBENCH}/cases/{case['id']}", headers=REVIEWER).status_code == 404
    assert api.get(f"{WORKBENCH}/cases/{case['id']}/history", headers=REVIEWER).status_code == 404
    assert api.get(f"{WORKBENCH}/encounters", headers=REVIEWER).json() == {"encounters": []}
    with pytest.raises(WorkbenchError, match="workbench_surface_disabled"):
        engine.pending_execution_ids()
    with pytest.raises(WorkbenchError, match="workbench_surface_disabled"):
        engine.reconcile_pending(case["id"])
    assert decision(api, case, status=404)["detail"] == "case_not_found"
    for path, payload in (
        ("/events", {}),
        ("/reviews", {"subject": subject, "title": "Test review", "kind": "strategic",
                      "reason": "Incorrect source", "evidence": {"artifact_ref": "TEST-01"}}),
        ("/chat", {"agent": "operations", "message": "Ask for a review"}),
        (f"/cases/{case['id']}/feedback", {"rating": "helpful"}),
        ("/approval-relay", {"envelope": {}, "signature": "a" * 64}),
    ):
        response = api.post(WORKBENCH + path, headers=GLOBAL, json=payload)
        assert response.status_code == 404, response.text
    assert api.get(WORKBENCH + "/profiles", headers=REVIEWER).status_code == 404
    with pytest.raises(ValueError, match="invalid CRM source"):
        Workbench(engine.store.path, crm_source="portfolio-review")
    with pytest.raises(ValueError, match="invalid trusted CRM sources"):
        Workbench(engine.store.path, crm_sources=("portfolio-review",))
    submit_sensitive(api)
    reconcile_portfolio_reviews(api.app)
    [dedicated] = queue(api)["cases"]
    with pytest.raises(WorkbenchError, match="case_not_found"):
        generic.get_case(dedicated["id"], actor)


def test_audited_workbench_fails_closed_while_journal_is_disabled(tmp_path):
    with pytest.raises(ValueError, match="FACTORY_WORKBENCH_CYBER_AUDIT=on"):
        Settings(demo_mode="never", auth_mode="proxy", bu_policy_path="/config/business-units.yaml",
                 workbench_mode="portfolio")
    api = client(tmp_path)
    submit_sensitive(api)
    assert reconcile_portfolio_reviews(api.app) == 0
    [case] = queue(api)["cases"]
    path = api.app.state.workbench.store.path
    assert set_journal_enabled(path, False)
    assert decision(api, case, status=503)["detail"] == "portfolio_security_journal_required"
    assert queue(api)["cases"][0]["status"] == "waiting_review"
    assert set_journal_enabled(path, True)
    assert decision(api, case)["status"] == "decided"
    assert set_journal_enabled(path, False)
    assert reconcile_portfolio_reviews(api.app) == 0
    assert snapshot(api, REL_MANAGER)["tasks"][0]["status"] == "workbench"
    assert set_journal_enabled(path, True)
    assert reconcile_portfolio_reviews(api.app) == 1
    events = list(iter_verified_events(path))
    [proposal] = [row for row in events if row["event"] == "case.proposed"]
    [receipt] = [row for row in events if row["event"] == "decision.recorded"]
    assert proposal["proposal_hash"] == receipt["proposal_hash"] == case["proposal_hash"]
    assert proposal["case_ref"] == receipt["case_ref"] and receipt["outcome"] == "approved"
    assert "Assessment report" not in str(events) and "independent" not in str(events)


def _ready_sensitive(api, project_id, *, index=3, technical=False, dependencies=None):
    row = task(api, index, project_id, REL, execution="sensitive", technical=technical,
               dependency_ids=dependencies or [])
    return assign(api, row, REL_MANAGER, "agent", "ag-support-supervisor")


def _recover(api, row, actor=GLOBAL, *, status=200, revision=None):
    return post(api, f"/tasks/{row['id']}/recover-workbench", actor,
                {"revision": row["revision"] if revision is None else revision}, status)


@pytest.mark.parametrize("mode", ["off", "protected_demo"])
def test_workbench_requires_portfolio_mode_at_every_sensitive_gate(tmp_path, mode):
    enabled = client(tmp_path)
    project_id = project(enabled, unit=REL)
    ready = _ready_sensitive(enabled, project_id)
    active = command(enabled, _ready_sensitive(enabled, project_id, index=4), REL_AGENT, "start")
    reviewing = command(enabled, _ready_sensitive(enabled, project_id, index=5, technical=True), REL_AGENT, "start")
    reviewing = command(enabled, reviewing, REL_AGENT, "submit")
    assert snapshot(enabled, REL_MANAGER)["capabilities"]["workbench_handoff"] is True

    api = client(tmp_path, mode=mode)
    before = snapshot(api, REL_MANAGER)
    assert before["capabilities"]["workbench_handoff"] is False
    for row, actor, action in ((ready, REL_AGENT, "start"), (active, REL_AGENT, "submit"),
                               (reviewing, REL_ARCHITECT, "approve")):
        assert row["workbench_required"] is True
        assert command(api, row, actor, action, status=409)["detail"] == "workbench_handoff_unavailable"
    assert snapshot(api, REL_MANAGER) == before
    assert api.app.state.portfolio.pending_workbench_reviews(linked=False, after=0, limit=10) == []
    rework = command(api, reviewing, REL_ARCHITECT, "request_changes")
    assert rework["status"] == "ready" and not rework["start_authorized"]
    with api.app.state.portfolio._transaction(write=False) as db:
        assert db.execute("SELECT 1 FROM workbench_authors WHERE task_id = ?", (reviewing["id"],)).fetchone() is None

    ordinary = task(api, 6, project_id, REL, execution="read_only", technical=True)
    ordinary = assign(api, ordinary, REL_MANAGER, "agent", "ag-support-supervisor")
    ordinary = command(api, ordinary, REL_MANAGER, "authorize_start")
    ordinary = command(api, ordinary, REL_AGENT, "start")
    ordinary = command(api, ordinary, REL_AGENT, "submit")
    assert ordinary["workbench_required"] is False
    assert command(api, ordinary, REL_ARCHITECT, "approve")["status"] == "done"


def _legacy_orphan(api, monkeypatch, *, technical=False, dependencies=None):
    with monkeypatch.context() as legacy:
        legacy.setattr(api.app.state.portfolio, "_needs_workbench", lambda db, row, action: False)
        row = _ready_sensitive(api, project(api, unit=REL), technical=technical, dependencies=dependencies)
        row = command(api, row, REL_AGENT, "start")
        return command(api, row, REL_AGENT, "submit", evidence=[
            {"kind": "work", "label": "Old evidence", "ref": "OLD-01"},
        ])


@pytest.mark.parametrize("technical", [False, True])
def test_global_admin_resets_only_exact_legacy_orphan_for_fresh_review(tmp_path, monkeypatch, technical):
    off = client(tmp_path, mode="off")
    orphan = _legacy_orphan(off, monkeypatch, technical=technical)
    dependent = task(off, 9, orphan["project_id"], REL, execution="read_only", dependency_ids=[orphan["id"]])
    assert orphan["status"] == ("review" if technical else "workbench")
    assert _recover(off, orphan, status=409)["detail"] == "workbench_handoff_unavailable"

    api = client(tmp_path)
    assert _recover(api, orphan, OPS_LEAD, status=403)["detail"] == "portfolio_forbidden"
    assert _recover(api, orphan, REL_MANAGER, status=403)["detail"] == "portfolio_forbidden"
    if technical:
        before = snapshot(api, REL_MANAGER)
        assert command(api, orphan, REL_ARCHITECT, "request_changes", status=409)[
            "detail"
        ] == "workbench_recovery_required"
        assert snapshot(api, REL_MANAGER) == before
    assert _recover(api, orphan, status=409, revision=orphan["revision"] - 1)["detail"] == "revision_conflict"
    ready = _recover(api, orphan)
    assert ready["status"] == "ready" and ready["id"] == orphan["id"]
    assert ready["revision"] == orphan["revision"] + 1 and ready["assignee"] == orphan["assignee"]
    assert ready["dependency_ids"] == orphan["dependency_ids"]
    assert ready["evidence"] == [] and ready["start_authorized"] is False
    assert ready["history"][0]["action"] == "recover_workbench" and ready["history"][0]["actor"] == "Direction"
    assert not api.app.state.workbench.store.path.exists()
    assert next(row for row in snapshot(api, REL_MANAGER)["tasks"] if row["id"] == dependent["id"])[
        "dependency_ids"
    ] == [orphan["id"]]
    assert _recover(api, orphan, status=409)["detail"] == "revision_conflict"
    disabled = client(tmp_path, mode="off")
    before = snapshot(disabled, REL_MANAGER)
    assert command(disabled, ready, REL_AGENT, "start", status=409)["detail"] == "workbench_handoff_unavailable"
    assert snapshot(disabled, REL_MANAGER) == before
    assert disabled.app.state.portfolio.pending_workbench_reviews(linked=False, after=0, limit=10) == []
    ready = command(api, ready, REL_AGENT, "start")
    ready = command(api, ready, REL_AGENT, "submit")
    if technical:
        assert ready["status"] == "review"
        ready = command(api, ready, REL_ARCHITECT, "approve")
    assert ready["status"] == "workbench"
    [outbox] = api.app.state.portfolio.pending_workbench_reviews(linked=False, after=0, limit=10)
    assert outbox.task_revision == ready["revision"]


def test_recovery_refuses_existing_author_outbox_and_independent_workbench_case(tmp_path, monkeypatch):
    off = client(tmp_path, mode="off")
    orphan = _legacy_orphan(off, monkeypatch)
    api = client(tmp_path)
    api.app.state.workbench._ensure_portfolio_review(
        subject=f"portfolio/{orphan['id']}/{orphan['revision']}", title="Existing review",
        reason="Durable case in the independent Workbench store.", evidence=[], exclusions=("fixture-author",),
    )
    assert _recover(api, orphan, status=409)["detail"] == "workbench_recovery_unavailable"

    project_id = project(api, index=10, unit=REL)
    valid = command(api, _ready_sensitive(api, project_id, technical=True), REL_AGENT, "start")
    valid = command(api, valid, REL_AGENT, "submit")
    assert _recover(api, valid, status=409)["detail"] == "workbench_recovery_unavailable"
    valid = command(api, valid, REL_ARCHITECT, "approve")
    assert _recover(api, valid, status=409)["detail"] == "workbench_recovery_unavailable"
    [outbox] = api.app.state.portfolio.pending_workbench_reviews(linked=False, after=0, limit=10)
    assert (outbox.task_id, outbox.task_revision) == (valid["id"], valid["revision"])
