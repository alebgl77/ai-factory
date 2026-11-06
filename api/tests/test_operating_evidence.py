"""Live observations and Shadow projections exercised with fictional credentials and receipts."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from app.operating_evidence import OperatingEvidence, configured_sources, project_catalogue
from app.shadow_ai import Observatory, _hash_journal, iter_verified_events
from tests.test_cyber_evidence import _call, _key
from tests.test_security import proxy, who
from tests.test_shadow_ai import ROUTE, decide, evidence, period, propose, usage

OPS_PUBLISHER = who("producer", "ops-publisher")
OPS_REVIEWER = who("reviewer", "ops-reviewer")
OPS_READER = who("ops-reader", "ops-reader")
RH_READER = who("rh-reader", "rh-reader")
POST_RELEASE = "/api/v1/operating-evidence/releases"
POST_SIGNAL = "/api/v1/operating-evidence/signals"


def scoped(tmp_path, monkeypatch, *, operating=True, shadow=False):
    monkeypatch.setenv("FACTORY_OPERATING_EVIDENCE_SOURCES",
                       '{"release-ci":["ops-publisher","rh-publisher"]}' if operating else "")
    assigned = {
        "ops-publisher": ("viewer", "cc-ops"), "rh-publisher": ("viewer", "cc-rh"),
        "rh-admin": ("admin", "cc-rh"),
        "ops-reviewer": ("approver", "cc-ops"), "ops-maintainer": ("maintainer", "cc-ops"),
        "ops-reader": ("viewer", "cc-ops"), "rh-reader": ("viewer", "cc-rh"),
        "obs-global-view": ("viewer", "*"), "obs-global-admin": ("admin", "*"),
        "obs-global-editor": ("editor", "*"), "obs-global-approver": ("approver", "*"),
        "obs-scoped-editor": ("editor", "cc-rh"),
    }
    policy = tmp_path / "bu.yaml"
    policy.write_text(json.dumps({
        "version": 1, "groups": {group: [center] for group, (_, center) in assigned.items()},
        "identities": {"reviewer": "p07"}, "shadow_teams": {"cc-ops": ["team-ops"], "cc-rh": ["team-rh"]},
    }), encoding="utf-8")
    return proxy(tmp_path, demo_mode="never", bu_policy_path=str(policy), shadow_ai_enabled=shadow,
                 role_map=tuple(f"{group}:{role}" for group, (role, _) in assigned.items()),
                 shadow_ai_groups=tuple(group for group in assigned if group.startswith("obs-") or group.endswith("-reader")))


def approved_case(client):
    case = client.post("/api/v1/registry/uc-01/model-version", json={"version": "approved-model"},
                       headers=who("maintainer", "ops-maintainer"))
    assert case.status_code == 200
    approval = client.post("/api/v1/registry/uc-01/assessment", headers=OPS_REVIEWER, json={
        "reviewer": "p07", "decision": "approved", "expected_review_revision": case.json()["review_revision"],
    })
    assert approval.status_code == 200
    assert approval.json()["assessment"]["separation_of_duties"] == "verified"
    return case.json()["review_revision"]


def common(revision, *, observation_id="observation-1"):
    now = dt.datetime.now(dt.timezone.utc)
    return {"usecase": "uc-01", "review_revision": revision, "model_version": "approved-model",
            "source": "release-ci", "observation_id": observation_id, "observed_at": now.isoformat()}


def release(revision):
    body = common(revision)
    at = (dt.datetime.fromisoformat(body["observed_at"]) - dt.timedelta(seconds=20)).isoformat()
    return {**body, "run_id": "run-1", "version": "release-2", "previous_version": "release-1",
            "artifact_digest": "a" * 64, "ingested_at": at, "indexed_at": at, "versioned_at": at,
            "evaluated_at": at, "metrics": {"faithfulness": 1, "answer_relevancy": 1, "context_precision": 1,
                                            "judge_score": 5, "redteam_pass": 1}}


def deploy_status(pipeline):
    item = pipeline["items"][0]
    return next(stage["status"] for stage in item["stages"] if stage["key"] == "deploy")


def test_release_proof_and_rollback_are_observed_not_inferred(tmp_path, monkeypatch):
    with scoped(tmp_path, monkeypatch) as client:
        revision = approved_case(client)
        body = release(revision)
        assert client.post(POST_RELEASE, json=body, headers=OPS_PUBLISHER).json() == {"status": "stored"}
        result = client.get("/api/v1/snapshot", headers=OPS_READER).json()
        assert result["meta"]["sections"]["pipeline"] == "live"
        assert result["pipeline"]["items"][0]["gate_passed"]
        assert deploy_status(result["pipeline"]) == "pending"
        assert result["pipeline"]["items"][0]["deployed_version"] is None

        observed = {**body, **common(revision, observation_id="observation-2"),
                    "online": {"thumbs_down_ratio": 2.6}}
        observed["deployment_receipt"] = {"digest": "b" * 64, "at": observed["observed_at"]}
        same_approver = who("reviewer", "ops-publisher", email="renamed-reviewer@example.org")
        assert client.post(POST_RELEASE, json=observed, headers=same_approver).status_code == 403
        effective = next(case for case in client.app.state.service.effective_usecases() if case["id"] == "uc-01")
        approval = effective["_review_approval"]
        premature_at = (dt.datetime.fromisoformat(approval["reviewed_at"]) - dt.timedelta(microseconds=1)).isoformat()
        premature = {**observed, **{key: premature_at for key in
                     ("ingested_at", "indexed_at", "versioned_at", "evaluated_at")},
                     "deployment_receipt": {"digest": "b" * 64, "at": premature_at}}
        rejection = client.post(POST_RELEASE, json=premature, headers=OPS_PUBLISHER)
        assert rejection.status_code == 409 and rejection.json()["detail"] == "receipt_before_approval"
        assert approval["subject_ref"] not in json.dumps(result)
        assert client.post(POST_RELEASE, json=observed, headers=OPS_PUBLISHER).json() == {"status": "stored"}
        assert client.post(POST_RELEASE, json=observed, headers=OPS_PUBLISHER).json() == {"status": "duplicate"}
        pipeline = client.get("/api/v1/pipeline", headers=OPS_READER).json()
        assert deploy_status(pipeline) == "passed"
        assert pipeline["items"][0]["deployed_version"] == "release-2"
        assert pipeline["rollbacks"][0]["status"] == "recommended" and not pipeline["rollbacks"][0]["auto"]
        assert client.get("/api/v1/pipeline", headers=RH_READER).json()["items"] == []

        changed = {**observed, "online": {"thumbs_down_ratio": 3.0}}
        assert client.post(POST_RELEASE, json=changed, headers=OPS_PUBLISHER).status_code == 409
        removed = {**body, **common(revision, observation_id="observation-3")}
        assert client.post(POST_RELEASE, json=removed, headers=OPS_PUBLISHER).status_code == 409
        rolled_back = {**observed, **common(revision, observation_id="observation-4"),
                       "rollback_receipt": {"digest": "c" * 64, "at": observed["observed_at"]}}
        assert client.post(POST_RELEASE, json=rolled_back, headers=OPS_PUBLISHER).json() == {"status": "stored"}
        pipeline = client.get("/api/v1/pipeline", headers=OPS_READER).json()
        assert pipeline["rollbacks"][0]["status"] == "observed" and not pipeline["rollbacks"][0]["auto"]
        assert pipeline["items"][0]["deployed_version"] == "release-1"

        client.post("/api/v1/registry/uc-01/model-version", json={"version": "next-model"},
                    headers=who("maintainer", "ops-maintainer"))
        assert client.post(POST_RELEASE, json=rolled_back, headers=OPS_PUBLISHER).status_code == 409
        assert client.get("/api/v1/pipeline", headers=OPS_READER).json()["items"] == []


def test_source_scope_and_drift_inputs_do_not_grant_actions(tmp_path, monkeypatch):
    with scoped(tmp_path, monkeypatch) as client:
        revision = approved_case(client)
        report = {**common(revision), "signal": "latency_drift", "value": 90.0}
        assert client.post(POST_SIGNAL, json=report, headers=OPS_READER).status_code == 403
        assert client.post(POST_SIGNAL, json={**report, "source": "unknown"}, headers=OPS_PUBLISHER).status_code == 403
        assert client.post(POST_SIGNAL, json={**report, "usecase": "uc-04"},
                           headers=who("both", "ops-publisher", "rh-reader")).status_code == 404
        assert client.post(POST_SIGNAL, json=report,
                           headers=who("both", "ops-publisher", "rh-admin")).status_code == 403
        assert client.post("/api/v1/registry/uc-01/model-version", json={"version": "forbidden"},
                           headers=OPS_PUBLISHER).status_code == 403
        for key, value in (("status", "done"), ("reviewed_by", "operator"), ("requested_action", "reindex"),
                           ("threshold", 1000), ("payload", "private text")):
            rejection = client.post(POST_SIGNAL, json={**report, key: value}, headers=OPS_PUBLISHER)
            assert rejection.status_code == 422 and rejection.json()["detail"] == "invalid_request"
        assert client.post(POST_SIGNAL, json=report, headers=OPS_PUBLISHER).status_code == 200
        events = client.get("/api/v1/dataloop", headers=OPS_READER).json()["events"]
        assert len(events) == 1 and events[0]["action"] == "human_review" and events[0]["status"] == "proposed"


def test_incomplete_stage_and_unapproved_receipt_fail_closed(tmp_path, monkeypatch):
    with scoped(tmp_path, monkeypatch) as client:
        uc = client.get("/api/v1/registry/uc-01", headers=OPS_READER).json()
        body = {**release(uc["review_revision"]), "model_version": uc["model"]["version"]}
        deployment = {**body, "deployment_receipt": {"digest": "b" * 64, "at": body["evaluated_at"]}}
        assert client.post(POST_RELEASE, json=deployment, headers=OPS_PUBLISHER).status_code == 409
        del body["indexed_at"]
        assert client.post(POST_RELEASE, json=body, headers=OPS_PUBLISHER).status_code == 422
        assert client.get("/api/v1/pipeline", headers=OPS_READER).json()["items"] == []


def test_source_run_and_observation_ids_are_namespaced_by_case_and_revision(tmp_path, monkeypatch):
    with scoped(tmp_path, monkeypatch) as client:
        for case_id, viewer, publisher, version in (
            ("uc-01", OPS_READER, OPS_PUBLISHER, "ops-release"),
            ("uc-04", RH_READER, who("rh-producer", "rh-publisher"), "rh-release"),
        ):
            case = client.get(f"/api/v1/registry/{case_id}", headers=viewer).json()
            body = {**release(case["review_revision"]), "usecase": case_id, "version": version,
                    "model_version": case["model"]["version"]}
            assert client.post(POST_RELEASE, json=body, headers=publisher).json() == {"status": "stored"}
        assert [row["version"] for row in client.get("/api/v1/pipeline", headers=OPS_READER).json()["items"]] == ["ops-release"]
        assert [row["version"] for row in client.get("/api/v1/pipeline", headers=RH_READER).json()["items"]] == ["rh-release"]
        update = client.post("/api/v1/registry/uc-01/model-version", json={"version": "next-model"},
                             headers=who("maintainer", "ops-maintainer")).json()
        revision = {**release(update["review_revision"]), "version": "next-release", "model_version": "next-model"}
        assert client.post(POST_RELEASE, json=revision, headers=OPS_PUBLISHER).json() == {"status": "stored"}
        assert [row["version"] for row in client.get("/api/v1/pipeline", headers=OPS_READER).json()["items"]] == ["next-release"]


def test_rejects_unreleased_evidence_schema_without_mutating_it(tmp_path):
    directory = tmp_path / "operating-evidence"
    directory.mkdir()
    path = directory / "observations.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE evidence (unreleased TEXT)")
    with pytest.raises(ValueError, match="unsupported development operating evidence schema"):
        OperatingEvidence(str(tmp_path), {})
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall() == [("evidence",)]


def test_shadow_team_roles_catalogue_and_sealed_legacy_chain(tmp_path, monkeypatch, capsys):
    with scoped(tmp_path, monkeypatch, operating=False, shadow=True) as client:
        store = client.app.state.shadow_ai
        with store._transaction(write=True) as db:
            store._journal(db, store._now(), "policy_approved", "legacy-proposal:legacy-tool", "system")
        admin, editor, reviewer = (who(role, f"obs-global-{role}") for role in ("admin", "editor", "approver"))
        assert client.post(f"{ROUTE}/periods", headers=admin, json=period(team_usage=(
            usage("team-ops", "tl-search"), usage("team-rh", "private-hr-tool"),
        ))).status_code == 200
        assert client.post(f"{ROUTE}/evidence", headers=admin, json=evidence()).status_code == 200
        visible = client.get(ROUTE, headers=OPS_READER).json()
        assert {row["team"] for row in visible["team_usage"]} == {"team-ops"} and visible["evidence"] is None
        split_role = who("split", "obs-global-view", "obs-scoped-editor")
        assert client.get(ROUTE, headers=split_role).json()["capabilities"]["propose"] is False
        assert propose(client, "tl-search", "restricted", headers=split_role).status_code == 403
        pending = propose(client, "tl-qdrant", "approved", headers=editor)
        assert decide(client, pending, headers=reviewer).status_code == 200
        pending = propose(client, "tl-search", "restricted", alternative="tl-qdrant", headers=editor)
        assert decide(client, pending, headers=reviewer).status_code == 200
        catalog = client.get("/api/v1/catalog", headers=who("wide", "obs-global-view")).json()
        tools = {tool["id"]: tool for tool in catalog["tools"]}
        assert tools["tl-search"]["shadow_policy"] == {"status": "restricted", "revision": 1,
                                                        "alternative": "tl-qdrant"}
        assert tools["tl-search"]["self_service"]["allowed"] is False
        assert "private-hr-tool" not in json.dumps(catalog)

        path = tmp_path / "state" / "shadow-ai" / "observatory.sqlite3"
        raw = list(iter_verified_events(path))
        decision = [event for event in raw if event["action"] == "policy_approved_v2"][-1]
        proposal_id = pending.json()["id"]
        content = {"v": 2, "proposal_id": proposal_id, "tool": "tl-search", "status": "restricted",
                   "alternative": "tl-qdrant", "expected_revision": 0, "decision": "approve"}
        proof = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert decision["object_id"] == f"{proposal_id}:{proof}"
        key, checkpoint = _key(tmp_path / "key"), tmp_path / "checkpoint.json"
        assert _call("seal", path, key, checkpoint, kind="shadow") == 0
        capsys.readouterr()
        assert _call("export", path, key, checkpoint, kind="shadow") == 0
        output = capsys.readouterr().out
        assert {row["event"] for row in map(json.loads, output.splitlines())} >= {"policy_approved", "policy_approved_v2"}
        assert "target_ref" in output
        assert all(secret not in output for secret in ("tl-search", "private-hr-tool", "team-rh", proposal_id, proof))
        with closing(sqlite3.connect(path)) as db:
            with db:
                db.execute("UPDATE journal SET object_id = 'altered' WHERE seq = ?", (decision["seq"],))
        assert _call("verify", path, key, checkpoint, kind="shadow") == 1
        assert "shadow_journal_invalid" in capsys.readouterr().err


def test_catalogue_never_grants_or_reveals_invisible_alternative():
    class Store:
        def catalogue_policies(self):
            return [{"tool": "blocked", "status": "restricted", "revision": 1, "alternative": "hidden"},
                    {"tool": "approved", "status": "approved", "revision": 2, "alternative": None},
                    {"tool": "hidden", "status": "approved", "revision": 2, "alternative": None}]

    before = {"tools": [{"id": "blocked", "self_service": {"allowed": True}},
                        {"id": "approved", "self_service": {"allowed": False}}],
              "agents": [{"id": "agent", "tools": ["blocked"], "self_service": {"allowed": True}}]}
    after = project_catalogue(before, Store())
    assert "hidden" not in json.dumps(after)
    assert not after["tools"][0]["self_service"]["allowed"] and not after["tools"][1]["self_service"]["allowed"]
    assert not after["agents"][0]["self_service"]["allowed"] and before["agents"][0]["self_service"]["allowed"]


def test_cyber_rejects_malformed_shadow_timestamp_even_if_rehashed(tmp_path, capsys):
    store = Observatory(str(tmp_path), 5)
    with store._transaction(write=True) as db:
        store._journal(db, store._now(), "policy_approved", "previous-event", "system")
        forged_at = "arbitrary sensitive content"
        forged_hash = _hash_journal(1, forged_at, "policy_approved", "previous-event", "system", "")
        db.execute("UPDATE journal SET at = ?, hash = ? WHERE seq = 1", (forged_at, forged_hash))
        db.execute("UPDATE settings SET journal_hash = ? WHERE id = 1", (forged_hash,))
    key, checkpoint = _key(tmp_path / "key"), tmp_path / "checkpoint.json"
    assert _call("seal", store.path, key, checkpoint, kind="shadow") == 1
    output = capsys.readouterr()
    assert "shadow_journal_invalid" in output.err and forged_at not in output.out + output.err
    assert not checkpoint.exists()


@pytest.mark.parametrize("raw", ['{"demo":["one"],"demo":["two"]}', '{"demo":["same","same"]}', '{"*":[]}', '[]'])
def test_rejects_ambiguous_evidence_source_configuration(raw):
    with pytest.raises(ValueError, match="invalid FACTORY_OPERATING_EVIDENCE_SOURCES"):
        configured_sources(raw)
