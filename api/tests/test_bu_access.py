"""Reproduce the cross-BU audit issues against authenticated API entry points."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.service import _TelemetrySample
from app.settings import Settings
from tests.conftest import NOW, TODAY
from tests.test_security import proxy, who

CONFIG = Path(__file__).resolve().parents[2] / "config"
API = "/api/v1"
SPACES = API + "/spaces/state"
ROLE_MAP = ("ops:editor", "finance:viewer", "finance-edit:editor", "finance-model:maintainer",
            "finance-review:approver", "rh-edit:editor", "global-read:viewer", "global-edit:editor",
            "global-admin:admin", "unscoped-admin:admin")
OPS = who("alice", "ops")
COLLEAGUE = who("bob", "ops")
FINANCE = who("carol", "finance")
FINANCE_EDITOR = who("carol", "finance-edit")
GLOBAL = who("operator", "global-admin")


def policy_file(tmp_path, **override):
    doc = {
        "version": 1,
        "groups": {"ops": ["cc-ops"], "finance": ["cc-finance"], "finance-edit": ["cc-finance"],
                   "finance-model": ["cc-finance"], "finance-review": ["cc-finance"], "rh-edit": ["cc-rh"],
                   "global-read": ["*"], "global-edit": ["*"], "global-admin": ["*"]},
        "resources": {"D1": ["cc-ops", "cc-finance"], "B1": ["cc-ops", "cc-finance", "cc-rh"], "B2": ["cc-rh"]},
        "secure_resources": ["B1", "B2"],
        "people": {"cc-ops": ["p01", "p02"], "cc-finance": ["p07"], "cc-rh": ["p06"]},
        "identities": {"alice": "p01", "bob": "p02", "carol": "p07", "dana": "p06"},
        "shadow_teams": {"cc-ops": ["delivery"], "cc-finance": ["finance"]},
        **override,
    }
    path = tmp_path / "business-units.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(path)


def client(tmp_path, **kwargs):
    return proxy(tmp_path, config_dir=str(CONFIG), role_map=ROLE_MAP, bu_policy_path=policy_file(tmp_path), **kwargs)


def booking(key, resource, case, person, start=540, end=600, **kwargs):
    return {"id": key, "resourceId": resource, "ucId": case, "employeeId": person,
            "date": TODAY.isoformat(), "activity": "delivery", "start": start, "end": end, **kwargs}


def write(api, headers, version, *bookings, view_token=None, **kwargs):
    if view_token is None:
        view_token = api.get(SPACES, headers=headers).json().get("view_token")
    return api.put(SPACES, headers=headers, json={"version": version, "view_token": view_token,
                   "state": {"v": 2, "bookings": list(bookings), **kwargs}})


def test_policy_fails_closed_before_accepting_requests(tmp_path):
    with pytest.raises(ValueError, match="requires FACTORY_BU_POLICY"):
        Settings(auth_mode="proxy", demo_mode="never")
    with pytest.raises(ValueError, match="invalid FACTORY_BU_POLICY"):
        proxy(tmp_path, config_dir=str(CONFIG), role_map=ROLE_MAP, bu_policy_path=policy_file(tmp_path, version=True))
    path = Path(policy_file(tmp_path))
    path.write_text("version: 1\nversion: 1\ngroups: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid FACTORY_BU_POLICY"):
        proxy(tmp_path, config_dir=str(CONFIG), role_map=ROLE_MAP, bu_policy_path=str(path))


def test_scope_is_applied_before_assembly_and_cache(tmp_path, monkeypatch):
    api = client(tmp_path, demo_mode="auto", prometheus_url="http://prometheus.invalid", cache_ttl_s=90)
    svc = api.app.state.service
    records = [{"date": TODAY.isoformat(), "usecase": usecase, "tokens_in": tokens, "llm_eur": euros}
               for usecase, tokens, euros in [("uc-01", 321, 1.25), ("uc-05", 654, 8.5), ("unknown", 999, 100)]]
    sample = _TelemetrySample({"prometheus": True}, {"usage": records, "grafana_dashboards": [{"title": "INTERNAL"}],
                                                    "litellm_models": [{"id": "INTERNAL"}]}, None)
    monkeypatch.setattr(svc, "_collect_telemetry", lambda *args: sample)
    first = api.get(API + "/snapshot", headers=OPS).json()
    other = api.get(API + "/snapshot", headers=FINANCE).json()
    first_again = api.get(API + "/snapshot", headers=OPS).json()
    assert first == first_again
    assert {row["cost_center"] for row in first["registry"]} == {"cc-ops"}
    assert {row["cost_center"] for row in other["registry"]} == {"cc-finance"}
    assert {row["id"] for row in first["profiles"]["people"]} == {"p01", "p02"}
    assert {row["cost_center"] for row in first["costs"]["by_cost_center"]} == {"cc-ops"}
    assert first["costs"]["totals"]["tokens_in"] == 321
    assert other["costs"]["totals"]["tokens_in"] == 654
    costs = api.get(API + "/costs?mode=chargeback", headers=OPS).json()
    assert costs["mode"] == "showback" and costs["scope"] == "attributed_only"
    assert costs["totals"]["total_eur"] == 1.25 and costs["totals"]["platform_eur"] == 0
    assert "INTERNAL" not in str(first)
    assert {"uc-01", "uc-05"} <= {row["id"] for row in api.get(API + "/registry", headers=GLOBAL).json()}
    assert "uc-01" not in api.get("/metrics").text


def test_case_and_platform_routes_bind_permission_to_the_same_group(tmp_path):
    api = client(tmp_path, workbench_mode="protected_demo")
    mixed = who("carol", "finance", "unscoped-admin")
    assert api.get(API + "/snapshot", headers=who("carol", "unscoped-admin")).status_code == 403
    assert api.get(API + "/registry/uc-05", headers=OPS).status_code == 404
    assert api.post(API + "/registry/uc-05/model-version", headers=mixed, json={"version": "2027-01"}).status_code == 403
    maintainer = who("carol", "finance-model")
    assert api.post(API + "/registry/uc-01/model-version", headers=maintainer, json={"version": "2027-01"}).status_code == 404
    assert api.post(API + "/registry/uc-05/model-version", headers=maintainer, json={"version": "2027-01"}).status_code == 200
    assert api.post(API + "/placement/decide", headers=OPS, json={"usecase": "uc-05"}).status_code == 404
    assert api.post(API + "/placement/decide", headers=FINANCE_EDITOR,
                    json={"usecase": "uc-05", "sensitivity": "C1"}).status_code == 403
    for route in ("/agentops", "/gpu", "/grafana/dashboards", "/grafana/metric/gpu_util", "/workbench/cases"):
        assert api.get(API + route, headers=OPS).status_code == 403, route
    workbench = api.get(API + "/workbench/cases", headers=who("alice", "global-read", "ops", "unscoped-admin"))
    assert workbench.status_code == 200
    assert not workbench.json()["capabilities"]["can_submit"] and not workbench.json()["capabilities"]["can_decide"]
    assert api.get(API + "/audit", headers=who("carol", "finance-review")).status_code == 403
    assert api.get(API + "/audit", headers=GLOBAL).status_code == 200


def test_scoped_booking_merge_preserves_hidden_rows_and_blocks_overlap(tmp_path):
    api = client(tmp_path)
    raw = api.app.state.service.state
    legacy = booking("old-legacy", "D1", "uc-01", "p01", start=720, end=780)
    raw.put_spaces_state(0, {"v": 2, "bookings": [legacy], "dayStatus": {"private": True}})
    assert api.get(SPACES, headers=OPS).json()["state"]["bookings"] == []
    finance = booking("f01", "B1", "uc-05", "p07", note="other-business-unit")
    ops = booking("o01", "D1", "uc-01", "p01")
    assert write(api, FINANCE_EDITOR, 1, finance).status_code == 200
    stale = write(api, OPS, 1, ops)
    assert stale.status_code == 409 and "other-business-unit" not in stale.text
    assert stale.json()["current"]["state"]["bookings"] == []
    assert write(api, OPS, 2, booking("clash", "B1", "uc-01", "p01")).status_code == 409
    assert write(api, OPS, 2, booking("clash-old", "D1", "uc-01", "p01", 730, 760)).status_code == 409
    assert write(api, OPS, 2, {**ops, "employeeId": "p02"}).status_code == 403
    assert write(api, OPS, 2, {**ops, "attendees": ["p07"]}).status_code == 403
    assert write(api, OPS, 2, ops, dayStatus={"wide": True}).status_code == 422
    assert write(api, FINANCE_EDITOR, 2, {**finance, "resourceId": "D1"}).status_code == 403
    saved = write(api, OPS, 2, ops)
    assert saved.status_code == 200
    assert saved.json()["scope"]["owned"] == ["o01"]
    assert "owners" not in saved.json() and "other-business-unit" not in saved.text
    assert write(api, COLLEAGUE, 3, {**ops, "note": "tampered"}).status_code == 403
    assert write(api, COLLEAGUE, 3).status_code == 200
    assert write(api, OPS, 4).status_code == 200
    assert {row["id"] for row in raw.spaces_state()["state"]["bookings"]} == {"old-legacy", "f01"}
    assert raw.spaces_state()["state"]["dayStatus"] == {"private": True}
    assert write(api, FINANCE, 5).status_code == 403
    recovery = API + "/spaces/recovery"
    assert api.get(recovery, headers=who("operator", "global-read", "unscoped-admin")).status_code == 403
    assert api.put(recovery, headers=who("operator", "global-edit"),
                   json={"version": 5, "state": {}}).status_code == 403
    assert {row["id"] for row in api.get(recovery, headers=GLOBAL).json()["state"]["bookings"]} == {"old-legacy", "f01"}
    assert api.put(recovery, headers=GLOBAL, json={"version": 5, "state": {}}).status_code == 200


def test_booking_view_cannot_be_replayed_after_identity_changes(tmp_path):
    api = client(tmp_path)
    owned = booking("colleague-booking", "D1", "uc-01", "p02")
    assert write(api, COLLEAGUE, 0, owned).status_code == 200
    alice = api.get(SPACES, headers=OPS).json()
    bob = api.get(SPACES, headers=COLLEAGUE).json()
    assert alice["version"] == bob["version"] == 1 and alice["view_token"] != bob["view_token"]
    replay = write(api, COLLEAGUE, 1, view_token=alice["view_token"])
    assert replay.status_code == 409 and replay.json() == {"detail": "booking_view_conflict"}
    assert api.get(SPACES, headers=COLLEAGUE).json()["scope"]["owned"] == ["colleague-booking"]
    assert write(api, COLLEAGUE, 1, view_token=bob["view_token"]).status_code == 200


def test_exact_approved_review_has_internal_clock_and_subject_proof(tmp_path):
    api = client(tmp_path)
    route = API + "/registry/uc-05"
    approver = who("carol", "finance-review", email="updated-contact@example.org")
    case = api.get(route, headers=FINANCE).json()
    revision = case["review_revision"]
    body = {"reviewer": "p07", "decision": "approved", "expected_review_revision": revision}
    email_only = {"X-Forwarded-Email": "updated-contact@example.org", "X-Forwarded-Groups": "finance-model"}
    assert api.post(route + "/model-version", headers=email_only, json={"version": "2027-03"}).status_code == 403
    email_only["X-Forwarded-Groups"] = "finance-review"
    assert api.post(route + "/assessment", headers=email_only, json=body).status_code == 403
    assert api.post(route + "/assessment", headers=approver, json={**body, "reviewer": "p01"}).status_code == 403
    assert api.post(route + "/assessment", headers=approver, json=body).status_code == 200
    svc = api.app.state.service
    effective = next(row for row in svc.effective_usecases() if row["id"] == "uc-05")
    proof = effective["_review_approval"]
    assert proof["reviewed_at"] == NOW.isoformat() and proof["revision"] == revision
    assert proof["subject_ref"] not in api.get(route, headers=FINANCE).text
    assert "updated-contact@example.org" not in api.get(route, headers=FINANCE).text
    assert api.post(route + "/model-version", headers=who("dana", "finance-model"), json={"version": "2027-02"}).status_code == 200
    effective = next(row for row in svc.effective_usecases() if row["id"] == "uc-05")
    assert "_review_approval" not in effective
