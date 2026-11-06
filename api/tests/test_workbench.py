"""Acceptance tests for the governed CRM-to-ERP sandbox."""

from __future__ import annotations

import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.security import Principal
from app.settings import Settings
from app.workbench.adapters import AdapterError, Odoo19Adapter

ROOT = "/api/v1/workbench"
PROXY = "10.89.255.2"


def opportunity(event_id="delivery-1", *, subject="opp-real-test", version=1, amount=128900):
    return {"specversion": "1.0", "source": "crm-sim", "id": event_id, "type": "crm.opportunity.won",
            "subject": subject, "data": {"title": "Atelier Acme · contract renewal",
                                         "customer_name": "Atelier Acme", "customer_ref": "CRM-ACME",
                                         "amount_minor": amount, "currency": "EUR", "record_version": version}}


def approval(case, *, decision="approve", rationale="Checked the synthetic order"):
    return {"decision": decision, "rationale": rationale, "revision": case["revision"],
            "proposal_hash": case["proposal_hash"]}


def identity(user, group):
    return {"X-Forwarded-User": user, "X-Forwarded-Email": f"{user}@example.test", "X-Forwarded-Groups": group}


def protected(tmp_path, transport=None, **options):
    app = create_app(Settings(state_dir=str(tmp_path), workbench_mode="protected_demo", auth_mode="proxy",
                              trusted_proxies=(f"{PROXY}/32",),
                              role_map=("submit:editor", "approve:approver", "both:admin", "view:viewer"),
                              **options), transport=transport)
    return TestClient(app, client=(PROXY, 12345))


def test_workbench_is_opt_in_and_protected_mode_needs_auth(make_client):
    assert make_client().get(ROOT + "/cases").status_code == 404
    client = make_client(workbench_mode="protected_demo")
    assert client.get(ROOT + "/cases").status_code == 401
    assert not client.app.state.workbench.store.path.exists()


def test_seed_and_unknown_customer_do_not_execute(make_client):
    client = make_client(workbench_mode="demo")
    first = client.get(ROOT + "/cases").json()
    second = client.get(ROOT + "/cases").json()
    assert first == second
    assert first["mode"] == "demo"
    assert {c["kind"] for c in first["cases"]} == {"action", "strategic", "undocumented"}
    event = opportunity()
    event["data"]["customer_ref"] = "OTHER-ORG"
    case = client.post(ROOT + "/events", json=event).json()
    assert case["kind"] == "undocumented"
    assert case["action"] is None
    decision = client.post(f"{ROOT}/cases/{case['id']}/decisions", json=approval(case))
    assert decision.json()["status"] == "decided"
    assert client.app.state.workbench.store.draft_count() == 0


def test_caller_cannot_preempt_reserved_demo_seed(make_client):
    client = make_client(workbench_mode="demo")
    for event in (opportunity("demo-event-042"), opportunity(subject="opp-demo-042")):
        response = client.post(ROOT + "/events", json=event)
        assert response.status_code == 422
        assert response.json()["detail"] == "reserved_demo_identity"
    assert len(client.get(ROOT + "/cases").json()["cases"]) == 3


def test_queue_priority_is_applied_before_page_cap(make_client):
    client = make_client(workbench_mode="demo")
    workbench = client.app.state.workbench
    actor = Principal("sandbox-user", frozenset({"admin"}), True, "proxy", user="sandbox-user")
    for i in range(201):
        workbench.submit(opportunity(f"delivery-{i}", subject=f"opp-queue-{i}"), actor)
    urgent = workbench.chat(actor, agent="governance", message="Revoir la stratégie CRM")
    queue = workbench.cases(actor)
    assert len(queue) == 200
    assert queue[0]["id"] == urgent["case_id"]


def test_event_deduplication_and_stale_proposal(make_client):
    client = make_client(workbench_mode="demo")
    first = client.post(ROOT + "/events", json=opportunity()).json()
    duplicate = client.post(ROOT + "/events", json=opportunity()).json()
    same_record = client.post(ROOT + "/events", json=opportunity("other-delivery")).json()
    assert first == duplicate == same_record
    changed_payload = client.post(ROOT + "/events", json=opportunity(amount=99))
    assert changed_payload.status_code == 409
    assert changed_payload.json()["detail"] == "event_conflict"
    new_revision = client.post(ROOT + "/events", json=opportunity("revision-2", version=2, amount=129000)).json()
    assert new_revision["id"] == first["id"]
    assert new_revision["proposal_hash"] != first["proposal_hash"]
    old_decision = client.post(f"{ROOT}/cases/{first['id']}/decisions", json=approval(first))
    assert old_decision.status_code == 409
    assert old_decision.json()["detail"] == "stale_proposal"
    assert client.app.state.workbench.store.draft_count() == 0


def test_expired_and_forged_source_cannot_execute(make_client):
    client = make_client(workbench_mode="demo")
    event = opportunity()
    case = client.post(ROOT + "/events", json=event).json()
    event["source"] = "other-crm"
    assert client.post(ROOT + "/events", json=event).status_code == 422
    workbench = client.app.state.workbench
    later = workbench.clock() + dt.timedelta(days=7)
    workbench.clock = lambda: later
    path = f"{ROOT}/cases/{case['id']}/decisions"
    assert client.post(path, json=approval(case)).json()["detail"] == "proposal_expired"
    assert client.post(path, json=approval(case, decision="reject")).json()["status"] == "rejected"
    assert workbench.store.draft_count() == 0


def test_concurrent_delivery_and_approval_make_one_draft(make_client):
    client = make_client(workbench_mode="demo", rate_limit_per_min=0)
    events = [opportunity(f"delivery-{i}") for i in range(8)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        cases = list(pool.map(lambda event: client.post(ROOT + "/events", json=event).json(), events))
    assert len({case["id"] for case in cases}) == 1
    case = cases[0]
    route = f"{ROOT}/cases/{case['id']}/decisions"
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: client.post(route, json=approval(case)), range(8)))
    assert all(r.status_code == 200 and r.json()["status"] == "executed" for r in responses)
    assert len({r.json()["receipt"]["id"] for r in responses}) == 1
    assert client.app.state.workbench.store.draft_count() == 1
    response = client.post(ROOT + "/events", json=opportunity("revision-2", version=2))
    assert response.status_code == 409
    assert response.json()["detail"] == "reconciliation_required"


def test_lost_receipt_recovers_after_process_restart(make_client, monkeypatch):
    first = make_client(workbench_mode="demo")
    store = first.app.state.workbench.store
    case = first.post(ROOT + "/events", json=opportunity()).json()
    original = store.create_draft

    def lose_receipt(*args, **kwargs):
        original(*args, **kwargs)
        raise TimeoutError

    monkeypatch.setattr(store, "create_draft", lose_receipt)
    path = f"{ROOT}/cases/{case['id']}/decisions"
    request = approval(case)
    response = first.post(path, json=request)
    assert response.status_code == 503
    assert response.json()["detail"] == "outcome_unknown_retry_same_approval"
    assert store.draft_count() == 1
    restarted = make_client(workbench_mode="demo")
    listed = restarted.get(ROOT + "/cases").json()["cases"]
    assert next(c for c in listed if c["id"] == case["id"])["status"] == "applying"
    result = restarted.post(path, json=request).json()
    assert result["status"] == "executed"
    assert result["receipt"]["state"] == "draft"
    assert restarted.app.state.workbench.store.draft_count() == 1


def test_role_checks_and_independent_approver(tmp_path):
    client = protected(tmp_path)
    assert client.get(ROOT + "/cases").status_code == 401
    viewer = identity("viewer", "view")
    admin = identity("requester", "both")
    approver = identity("approver", "approve")
    assert client.post(ROOT + "/events", json=opportunity(), headers=viewer).status_code == 403
    case = client.post(ROOT + "/events", json=opportunity(), headers=admin).json()
    route = f"{ROOT}/cases/{case['id']}/decisions"
    assert client.post(route, json=approval(case), headers=viewer).status_code == 403
    self_approval = client.post(route, json=approval(case), headers=admin)
    assert self_approval.status_code == 403
    assert self_approval.json()["detail"] == "self_approval_forbidden"
    approved = client.post(route, json=approval(case), headers=approver)
    assert approved.status_code == 200
    assert approved.json()["status"] == "executed"
    history = f"{ROOT}/cases/{case['id']}/history"
    assert client.get(history, headers=approver).json()["decisions"][0]["rationale"] == approval(case)["rationale"]
    viewer_history = client.get(history, headers=viewer).json()
    assert "rationale" not in viewer_history["decisions"][0] and "actor" not in viewer_history["decisions"][0]
    assert client.app.state.workbench.store.path.name == "workbench-protected_demo.sqlite3"


def test_same_origin_json_and_sanitized_validation_errors(make_client):
    client = make_client(workbench_mode="demo")
    assert client.post(ROOT + "/events", json=opportunity(), headers={"Origin": "http://other.test"}).status_code == 403
    assert client.post(ROOT + "/events", json=opportunity(), headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post(ROOT + "/events", content=json.dumps(opportunity()),
                       headers={"Content-Type": "text/plain"}).status_code == 415
    accepted = client.post(ROOT + "/events", json=opportunity(), headers={"Origin": "http://testserver"})
    assert accepted.status_code == 200
    rejected = client.post(ROOT + "/chat", json={"agent": "SECRET-INPUT-SENTINEL", "message": "private"})
    assert rejected.status_code == 422
    assert rejected.json() == {"detail": "invalid_request"}


def test_unpaired_unicode_rejected_without_persistence(make_client):
    client = make_client(workbench_mode="demo")
    event = opportunity()
    case = client.post(ROOT + "/events", json=event).json()
    event["data"]["title"] = "\ud800"
    endpoint = f"{ROOT}/cases/{case['id']}"
    bodies = [
        (ROOT + "/events", event),
        (endpoint + "/decisions", approval(case, rationale="\ud800")),
        (endpoint + "/feedback", {"rating": "helpful", "comment": "\ud800"}),
        (ROOT + "/chat", {"agent": "finance", "message": "\ud800"}),
    ]
    for route, body in bodies:
        response = client.post(route, content=json.dumps(body), headers={"Content-Type": "application/json"})
        assert response.status_code == 422
        assert response.json()["detail"] in {"invalid_event", "invalid_utf8", "invalid_request"}
    assert client.get(endpoint + "/history").json()["decisions"] == []
    assert client.app.state.workbench.store.draft_count() == 0


def test_odoo_rejects_unpaired_unicode_before_returning_partner():
    payload = [{"id": 1, "name": "\ud800", "ref": None, "write_date": "2026-11-01 13:00:00"}]
    response = httpx.Response(200, content=json.dumps(payload).encode())
    with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client:
        adapter = Odoo19Adapter("https://erp.example.test", "sandbox", "test-token", client=client)
        with pytest.raises(AdapterError, match="invalid or unordered partner page"):
            adapter.partners_page()


def test_chat_escalation_feedback_and_human_decision(make_client):
    client = make_client(workbench_mode="demo")
    case = client.post(ROOT + "/events", json=opportunity()).json()
    normal = client.post(ROOT + "/chat", json={"agent": "finance", "message": "Quel est le statut ?",
                                              "case_id": case["id"]}).json()
    assert not normal["escalated"]
    assert normal["route"]["model_id"] == "demo-rules"
    question = client.post(ROOT + "/chat", json={"agent": "governance", "message": "Revoir la stratégie CRM",
                                                "case_id": case["id"]}).json()
    assert question["escalated"]
    decisions = client.get(ROOT + "/cases").json()["cases"]
    review = next(c for c in decisions if c["id"] == question["case_id"])
    assert review["kind"] == "strategic"
    assert review["action"] is None
    reply = client.post(f"{ROOT}/cases/{review['id']}/feedback", json={"rating": "helpful", "comment": "Vérifié"})
    assert reply.json() == {"recorded": True}
    history = client.get(f"{ROOT}/cases/{review['id']}/history").json()
    assert history["my_feedback"]["comment"] == "Vérifié"
    assert history["feedback_counts"] == {"helpful": 1}
    decision = client.post(f"{ROOT}/cases/{review['id']}/decisions", json=approval(review)).json()
    assert decision["status"] == "decided"
    assert decision["receipt"] is None


def test_local_chat_fixed_model_and_no_cloud_fallback(tmp_path):
    model_requests = []
    success = True

    def gateway(request):
        model_requests.append(json.loads(request.content))
        assert request.url.path == "/v1/chat/completions"
        if not success:
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Advisory answer"}}]})

    client = protected(tmp_path, httpx.MockTransport(gateway), workbench_chat_mode="local",
                       litellm_url="http://litellm:4000", litellm_master_key="test-key")
    submitter = identity("submitter", "submit")
    viewer = identity("viewer", "view")
    case = client.post(ROOT + "/events", json=opportunity(), headers=submitter).json()
    request = {"agent": "finance", "message": "Quel est le montant ?", "case_id": case["id"]}
    assert client.post(ROOT + "/chat", json=request).status_code == 401
    result = client.post(ROOT + "/chat", json=request, headers=viewer)
    assert result.json()["source"] == "local"
    assert result.json()["answer"] == "Advisory answer"
    assert model_requests[0]["model"] == "chat-sensitive"
    assert model_requests[0]["metadata"]["sensitivity"] == "C3"
    assert model_requests[0]["metadata"]["turn_off_message_logging"]
    assert "tools" not in model_requests[0]
    success = False
    unavailable = client.post(ROOT + "/chat", json=request, headers=viewer)
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == "local_agent_unavailable"
    assert len(model_requests) == 2


def test_invalid_mode_and_anonymous_local_chat_fail_closed():
    with pytest.raises(ValueError, match="FACTORY_WORKBENCH_MODE"):
        Settings(workbench_mode="live")
    with pytest.raises(ValueError, match="requires protected_demo"):
        Settings(workbench_mode="demo", workbench_chat_mode="local")
    with pytest.raises(ValueError, match="internal litellm"):
        Settings(workbench_mode="protected_demo", auth_mode="proxy", workbench_chat_mode="local",
                 litellm_url="https://example.com", litellm_master_key="test-key")
