"""The 3D view may observe a protected Workbench revision without changing it."""

import sqlite3
from contextlib import closing

from ai_factory_workbench import Workbench
from ai_factory_workbench.identity import Actor
from app.routers.workbench import configure_workbench_surfaces
from tests.test_bu_access import OPS, client
from tests.test_security import who
from tests.test_workbench import ROOT, approval, identity, opportunity, protected

ENCOUNTERS = f"{ROOT}/encounters"
VIEWER = identity("viewer", "view")
SUBMITTER = identity("submitter", "submit")
APPROVER = identity("reviewer", "approve")


def test_encounters_requires_protected_authenticated_workbench_and_global_read(make_client, tmp_path):
    assert make_client().get(ENCOUNTERS).status_code == 404
    demo = make_client(workbench_mode="demo")
    assert demo.get(ENCOUNTERS).status_code == 404
    assert not demo.app.state.workbench.store.path.exists()
    assert make_client(workbench_mode="protected_demo").get(ENCOUNTERS).status_code == 401

    api = client(tmp_path, workbench_mode="protected_demo")
    assert api.get(ENCOUNTERS).status_code == 401
    assert api.get(ENCOUNTERS, headers=OPS).status_code == 403
    assert api.get(ENCOUNTERS, headers=OPS, params={"case_id": "0" * 32}).status_code == 403
    assert api.get(ENCOUNTERS, headers=who("reader", "global-read")).json() == {"encounters": []}
    assert not api.app.state.workbench.store.path.exists()


def test_encounters_is_a_read_only_projection(tmp_path, monkeypatch):
    api = protected(tmp_path)
    path = tmp_path / "state & #1" / "tenant.sqlite3"
    engine = Workbench(path, mode="protected_demo", workspace="platform-alpha")
    configure_workbench_surfaces(api.app, engine=engine)

    def unexpected_write():
        raise AssertionError("an encounter read attempted to initialize or seed the store")

    with monkeypatch.context() as read_only:
        read_only.setattr(engine.store, "_initialize", unexpected_write)
        read_only.setattr(engine, "seed", unexpected_write)
        response = api.get(ENCOUNTERS, headers=VIEWER)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {"encounters": []}
        missing = api.get(ENCOUNTERS, headers=VIEWER, params={"case_id": "0" * 32})
        assert missing.status_code == 200
        assert missing.json() == {"encounters": []}
        assert not path.parent.exists()

    event = opportunity()
    event["data"]["customer_ref"] = "CUSTOMER-SENSITIVE"
    submitted = api.post(ROOT + "/events", json=event, headers=SUBMITTER)
    assert submitted.status_code == 200
    case = submitted.json()
    before = path.read_bytes()

    reopened = Workbench(path, mode="protected_demo", workspace="platform-alpha")
    configure_workbench_surfaces(api.app, engine=reopened)
    monkeypatch.setattr(reopened.store, "_initialize", unexpected_write)
    monkeypatch.setattr(reopened, "seed", unexpected_write)
    expected = {"encounters": [{"case_id": case["id"], "revision": 1, "status": "waiting"}]}
    for params in ({}, {"case_id": case["id"]}):
        response = api.get(ENCOUNTERS, headers=VIEWER, params=params)
        assert response.status_code == 200
        assert response.json() == expected
    assert path.read_bytes() == before

    other = Workbench(path, mode="protected_demo", workspace="platform-beta")
    configure_workbench_surfaces(api.app, engine=other)
    response = api.get(ENCOUNTERS, headers=VIEWER)
    assert response.status_code == 409
    assert response.json() == {"detail": "workspace_mismatch"}
    exact = api.get(ENCOUNTERS, headers=VIEWER, params={"case_id": case["id"]})
    assert exact.status_code == 409
    assert exact.json() == response.json()


def test_encounters_exact_lookup_keeps_a_terminal_case_outside_the_bounded_queue(tmp_path):
    api = protected(tmp_path)
    engine = api.app.state.workbench
    actor = Actor("submitter", frozenset({"editor"}))
    event = opportunity("followed", subject="followed")
    event["data"]["customer_ref"] = "OTHER-ORG"
    followed = engine.submit(event, actor)
    for index in range(101):
        engine.submit(opportunity(f"delivery-{index}", subject=f"encounter-{index}"), actor)
    decision = api.post(f"{ROOT}/cases/{followed['id']}/decisions", json=approval(followed), headers=APPROVER)
    assert decision.status_code == 200
    response = api.get(ENCOUNTERS, headers=VIEWER)
    assert response.status_code == 200
    assert len(response.json()["encounters"]) == 100
    assert followed["id"] not in {row["case_id"] for row in response.json()["encounters"]}
    exact = api.get(ENCOUNTERS, headers=VIEWER, params={"case_id": followed["id"]})
    assert exact.status_code == 200
    assert exact.json() == {"encounters": [{"case_id": followed["id"], "revision": 1, "status": "approved"}]}
    assert engine.store.draft_count() == 0


def test_encounters_selector_cannot_distinguish_another_workspace_from_a_missing_case(tmp_path):
    api = protected(tmp_path)
    actor = Actor("submitter", frozenset({"editor"}))
    current = api.app.state.workbench
    visible = current.submit(opportunity("visible"), actor)
    separate = Workbench(tmp_path / "separate.sqlite3", mode="protected_demo", workspace="platform-beta")
    hidden = separate.submit(opportunity("hidden"), actor)
    for case_id in (hidden["id"], "0" * 32):
        response = api.get(ENCOUNTERS, headers=VIEWER, params={"case_id": case_id})
        assert response.status_code == 200
        assert response.json() == {"encounters": []}
    for params in ({"case_id": "A" * 32}, {"case_id": ""}, {"case_id": "0" * 31},
                   [("case_id", hidden["id"]), ("case_id", visible["id"])]):
        response = api.get(ENCOUNTERS, headers=VIEWER, params=params)
        assert response.status_code == 422
        assert response.json() == {"detail": "invalid_request"}
    assert len(api.get(ENCOUNTERS, headers=VIEWER).json()["encounters"]) == 1


def test_encounters_fails_closed_on_an_unbound_legacy_store(tmp_path):
    api = protected(tmp_path)
    path = api.app.state.workbench.store.path
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE cases (id TEXT PRIMARY KEY)")
    before = path.read_bytes()
    response = api.get(ENCOUNTERS, headers=VIEWER)
    assert response.status_code == 503
    assert response.json() == {"detail": "workbench_unavailable"}
    assert path.read_bytes() == before


def test_encounters_reports_the_exact_current_revision_and_decision(tmp_path):
    api = protected(tmp_path)

    def submit(event_id, *, subject, version=1):
        event = opportunity(event_id, subject=subject, version=version)
        event["data"]["customer_ref"] = "OTHER-ORG"
        response = api.post(ROOT + "/events", json=event, headers=SUBMITTER)
        assert response.status_code == 200
        return response.json()

    def decide(case, decision):
        route = f"{ROOT}/cases/{case['id']}/decisions"
        response = api.post(route, json=approval(case, decision=decision), headers=APPROVER)
        assert response.status_code == 200

    def states():
        response = api.get(ENCOUNTERS, headers=VIEWER)
        assert response.status_code == 200
        return {item["case_id"]: (item["revision"], item["status"]) for item in response.json()["encounters"]}

    first = submit("edit-1", subject="edit-review")
    second = submit("reject-1", subject="reject-review")
    assert states() == {first["id"]: (1, "waiting"), second["id"]: (1, "waiting")}

    decide(first, "request_changes")
    decide(second, "reject")
    assert states() == {first["id"]: (1, "changes_requested"), second["id"]: (1, "rejected")}

    revised = submit("edit-2", subject="edit-review", version=2)
    assert revised["id"] == first["id"]
    assert states() == {first["id"]: (2, "waiting"), second["id"]: (1, "rejected")}
    exact = api.get(ENCOUNTERS, headers=VIEWER, params={"case_id": first["id"]})
    assert exact.status_code == 200
    assert exact.json() == {"encounters": [{"case_id": first["id"], "revision": 2, "status": "waiting"}]}
    decide(revised, "approve")
    assert states() == {first["id"]: (2, "approved"), second["id"]: (1, "rejected")}
    assert api.app.state.workbench.store.draft_count() == 0


def test_encounters_does_not_report_applying_as_a_completed_decision(tmp_path):
    api = protected(tmp_path)
    engine = Workbench(api.app.state.workbench.store.path, mode="protected_demo", defer_execution=True)
    configure_workbench_surfaces(api.app, engine=engine)
    case = api.post(ROOT + "/events", json=opportunity(), headers=SUBMITTER).json()
    response = api.post(f"{ROOT}/cases/{case['id']}/decisions", json=approval(case), headers=APPROVER)
    assert response.status_code == 200
    assert response.json()["status"] == "applying"
    assert api.get(ENCOUNTERS, headers=VIEWER).json() == {
        "encounters": [{"case_id": case["id"], "revision": 1, "status": "waiting"}],
    }
    assert engine.store.draft_count() == 0

    engine.reconcile_pending(case["id"])
    assert api.get(ENCOUNTERS, headers=VIEWER).json() == {
        "encounters": [{"case_id": case["id"], "revision": 1, "status": "approved"}],
    }
    assert engine.store.draft_count() == 1
