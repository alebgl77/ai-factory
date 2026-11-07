"""Only observed protected Workbench activity reaches the activity producer."""

import datetime as dt
import json
import time

import httpx
import pytest

from app.routers import workbench
from tests.test_workbench import ROOT, approval, identity, opportunity, protected

VIEWER = identity("viewer", "view")
EDITOR = identity("operator", "submit")
APPROVER = identity("reviewer", "approve")
CHAT = f"{ROOT}/chat"


@pytest.fixture
def captured(monkeypatch):
    events = []

    def record(_request, access, event):
        events.append((access.principal.user, event))
        return True

    monkeypatch.setattr(workbench, "record_workbench_activity", record)
    return events


def _prepare(api, *, known_customer=True):
    event = opportunity()
    if not known_customer:
        event["data"]["customer_ref"] = "OTHER-ORG"
    response = api.post(ROOT + "/events", json=event, headers=EDITOR)
    assert response.status_code == 200
    return response.json()


def _local_api(tmp_path, gateway):
    return protected(tmp_path, httpx.MockTransport(gateway), workbench_chat_mode="local",
                     litellm_url="http://litellm:4000", litellm_master_key="provider-key")


def _chat(case):
    return {"agent": "finance", "message": "Quel est le montant de ce dossier confidentiel ?", "case_id": case["id"]}


@pytest.mark.parametrize(("usage", "expected"), [
    ({"prompt_tokens": 13, "completion_tokens": 4, "total_tokens": 17, "details": "ignored"},
     {"input": 13, "output": 4, "total": 17}),
    ({"prompt_tokens": 0}, {"input": 0}),
    ({"prompt_tokens": 4, "completion_tokens": 2}, {"input": 4, "output": 2}),
    (None, None),
    ("malformed", None),
    ({"prompt_tokens": 4, "completion_tokens": True}, None),
    ({"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 10}, None),
    ({"total_tokens": -1}, None),
    ({"total_tokens": 1_000_000_001}, None),
])
def test_local_advice_uses_provider_usage_and_actual_elapsed_time(tmp_path, captured, usage, expected):
    calls = []

    def gateway(request):
        calls.append(request)
        time.sleep(0.02)
        payload = {"choices": [{"message": {"content": "Confidential answer"}}]}
        if usage is not None:
            payload["usage"] = usage
        return httpx.Response(200, json=payload)

    api = _local_api(tmp_path, gateway)
    case = _prepare(api)
    started = dt.datetime.now(dt.timezone.utc)
    response = api.post(CHAT, json=_chat(case), headers=VIEWER)
    finished = dt.datetime.now(dt.timezone.utc)
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Confidential answer" and body["source"] == "local"
    assert set(body) == {"answer", "source", "route", "case_id", "escalated"}
    assert len(calls) == 1
    [(owner, event)] = captured
    assert owner == "viewer"
    assert event["actor_type"] == "agent" and event["actor_id"] == "finance"
    assert event["status"] == "completed" and event["tokens"] == expected
    assert event["supports"] == [{"kind": "work", "label": "Dossier Workbench", "ref": case["id"]}]
    assert set(event) == {"id", "actor_type", "actor_id", "action", "started_at", "finished_at", "status",
                          "tokens", "supports"}
    event_started = dt.datetime.fromisoformat(event["started_at"])
    event_finished = dt.datetime.fromisoformat(event["finished_at"])
    assert started <= event_started < event_finished <= finished
    assert (event_finished - event_started).total_seconds() >= 0.02
    saved = json.dumps(event)
    for sensitive in (_chat(case)["message"], body["answer"], "provider-key", case["title"], case["proposal_hash"]):
        assert sensitive not in saved


def test_failed_provider_is_observed_without_fallback_and_legacy_advisor_stays_supported(tmp_path, captured):
    calls = []

    def gateway(request):
        calls.append(request)
        return httpx.Response(503)

    api = _local_api(tmp_path, gateway)
    case = _prepare(api)
    assert api.post(CHAT, json=_chat(case)).status_code == 401
    response = api.post(CHAT, json=_chat(case), headers=VIEWER)
    assert response.status_code == 503
    assert response.json() == {"detail": "local_agent_unavailable"}
    assert len(calls) == 1
    assert [event["status"] for _, event in captured] == ["failed"]
    assert captured[0][1]["tokens"] is None

    api.app.state.workbench.advisor = lambda _agent, _message, _record: "Legacy plain string"
    legacy = api.post(CHAT, json=_chat(case), headers=VIEWER)
    assert legacy.status_code == 200 and legacy.json()["answer"] == "Legacy plain string"
    assert len(calls) == 1
    assert captured[1][1]["status"] == "completed" and captured[1][1]["tokens"] is None


def test_human_decision_records_processing_once_and_simulation_is_ignored(tmp_path, captured, make_client):
    api = protected(tmp_path)
    case = _prepare(api, known_customer=False)
    response = api.post(CHAT, json=_chat(case), headers=VIEWER)
    assert response.status_code == 200 and response.json()["source"] == "simulation"
    assert captured == []

    route = f"{ROOT}/cases/{case['id']}/decisions"
    payload = approval(case, rationale="Sensitive rationale")
    assert api.post(route, json=payload, headers=VIEWER).status_code == 403
    assert captured == []
    response = api.post(route, json=payload, headers=APPROVER)
    assert response.status_code == 200 and response.json()["status"] == "decided"
    assert api.post(route, json=payload, headers=APPROVER).json() == response.json()
    [(owner, event)] = captured
    assert owner == "reviewer"
    assert event["actor_type"] == "user" and "actor_id" not in event
    assert event["status"] == "completed" and event["tokens"] is None
    assert event["started_at"] is None and dt.datetime.fromisoformat(event["finished_at"]).tzinfo == dt.timezone.utc
    assert event["supports"] == [{"kind": "work", "label": "Dossier Workbench", "ref": case["id"]}]
    assert payload["rationale"] not in json.dumps(event)
    assert api.app.state.workbench.store.draft_count() == 0

    demo = make_client(workbench_mode="demo")
    fictional = demo.post(ROOT + "/events", json=opportunity()).json()
    assert demo.post(CHAT, json=_chat(fictional)).json()["source"] == "simulation"
    assert demo.post(f"{ROOT}/cases/{fictional['id']}/decisions", json=approval(fictional)).status_code == 200
    assert len(captured) == 1


def test_activity_write_failure_preserves_advice_errors_and_decisions(tmp_path, monkeypatch):
    def unavailable(*_args):
        raise OSError("activity storage unavailable")

    monkeypatch.setattr(workbench, "record_workbench_activity", unavailable)
    responses = iter((httpx.Response(200, json={"choices": [{"message": {"content": "Advice"}}]}),
                      httpx.Response(503)))
    api = _local_api(tmp_path, lambda _: next(responses))
    case = _prepare(api)
    assert api.post(CHAT, json=_chat(case), headers=VIEWER).json()["answer"] == "Advice"
    failed = api.post(CHAT, json=_chat(case), headers=VIEWER)
    assert failed.status_code == 503 and failed.json()["detail"] == "local_agent_unavailable"

    route = f"{ROOT}/cases/{case['id']}/decisions"
    first = api.post(route, json=approval(case), headers=APPROVER)
    repeat = api.post(route, json=approval(case), headers=APPROVER)
    assert first.status_code == 200 and first.json()["status"] == "executed"
    assert repeat.json() == first.json()
    assert api.app.state.workbench.store.draft_count() == 1


def test_committed_decision_is_recorded_once_when_erp_dispatch_times_out(tmp_path, monkeypatch):
    api = protected(tmp_path)
    case = _prepare(api)
    case_route = f"{ROOT}/cases/{case['id']}"
    decision_route = f"{case_route}/decisions"
    payload = approval(case)
    store = api.app.state.workbench.store

    def timeout(*_args, **_kwargs):
        raise TimeoutError("ERP sandbox unavailable")

    with monkeypatch.context() as unavailable:
        unavailable.setattr(store, "create_draft", timeout)
        first = api.post(decision_route, json=payload, headers=APPROVER)
    assert first.status_code == 503
    assert first.json() == {"detail": "outcome_unknown_retry_same_approval"}
    assert store.draft_count() == 0
    still_pending = api.get(case_route, headers=APPROVER).json()
    assert still_pending["status"] == "applying" and still_pending["receipt"] is None

    activity = "/api/v1/activity"
    [decision] = api.get(activity, headers=APPROVER).json()["events"]
    assert decision["action"] == "Décision Workbench" and decision["status"] == "completed"
    assert decision["duration_ms"] is None and decision["tokens"] is None
    assert decision["supports"] == [{"kind": "work", "label": "Dossier Workbench", "ref": case["id"]}]

    retry = api.post(decision_route, json=payload, headers=APPROVER)
    assert retry.status_code == 200 and retry.json()["status"] == "executed"
    assert store.draft_count() == 1
    assert api.get(activity, headers=APPROVER).json()["events"] == [decision]


def test_workbench_activity_persists_real_advice_and_decision_under_separate_owners(tmp_path):
    api = _local_api(tmp_path, lambda _: httpx.Response(200, json={
        "choices": [{"message": {"content": "Advice"}}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 2},
    }))
    case = _prepare(api)
    assert api.post(CHAT, json=_chat(case), headers=VIEWER).status_code == 200
    decision = f"{ROOT}/cases/{case['id']}/decisions"
    assert api.post(decision, json=approval(case), headers=APPROVER).status_code == 200

    activity = "/api/v1/activity"
    viewer_log = api.get(activity, headers=VIEWER)
    reviewer_log = api.get(activity, headers=APPROVER)
    assert viewer_log.status_code == reviewer_log.status_code == 200
    [advice] = viewer_log.json()["events"]
    [review] = reviewer_log.json()["events"]
    assert advice["actor_id"] == "finance" and advice["user_id"] == "viewer"
    assert advice["source"] == "workbench" and advice["duration_ms"] is not None
    assert advice["tokens"] == {"input": 4, "output": 2, "total": None}
    assert review["actor_id"] == review["user_id"] == "reviewer"
    assert review["source"] == "workbench" and review["duration_ms"] is None and review["tokens"] is None
    assert advice["supports"] == review["supports"] == [{"kind": "work", "label": "Dossier Workbench",
                                                         "ref": case["id"]}]
