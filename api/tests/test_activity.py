"""Check private work history at the real proxy-authenticated HTTP boundary."""

from __future__ import annotations

import concurrent.futures
import json
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi import Request

from app import activity
from app.security import READ, Access, Principal
from tests.test_bu_access import OPS, client as bu_client
from tests.test_security import UNTRUSTED_PEER, build, proxy, who

API = "/api/v1/activity"
ALICE = who("alice", "grp-editor")
BOB = who("bob", "grp-editor")
ADMIN = who("operator", "grp-admin")


def event(sequence: int = 1, **changes):
    return {
        "id": f"{sequence:032x}",
        "actor_type": "agent",
        "actor_id": "legal-assistant",
        "action": "Analyse d’un document",
        "status": "completed",
        "started_at": "2026-11-03T09:56:00+01:00",
        "finished_at": "2026-11-03T09:58:01.250+01:00",
        "tokens": {"input": 90, "output": 10},
        "supports": [{"kind": "file", "label": "Rapport A.pdf", "ref": "doc-a"}],
        **changes,
    }


def post(api, headers=ALICE, **changes):
    return api.post(API, headers=headers, json=event(**changes))


def rows(api, headers=ALICE, **params):
    response = api.get(API, headers=headers, params=params)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def test_no_demo_write_no_untrusted_identity_and_empty_read_is_read_only(tmp_path):
    demo = build(tmp_path)
    assert demo.get(API, headers=ALICE).status_code == 404
    assert post(demo).status_code == 404
    assert not (tmp_path / "state" / "activity.sqlite3").exists()

    api = proxy(tmp_path)
    assert api.get(API).status_code == 401
    assert api.get(API, headers={"X-Forwarded-Email": "alice@example.org",
                                 "X-Forwarded-Groups": "grp-viewer"}).status_code == 403
    assert proxy(tmp_path, UNTRUSTED_PEER).get(API, headers=ALICE).status_code == 401
    assert post(api, who("alice", "grp-viewer")).status_code == 403
    empty = rows(api)
    assert empty["events"] == [] and not empty["can_view_all"]
    assert not (tmp_path / "state" / "activity.sqlite3").exists()


def test_private_attribution_utc_and_unknown_metrics(tmp_path):
    api = proxy(tmp_path)
    assert post(api).json() == {"recorded": True, "duplicate": False}
    assert post(api, BOB, sequence=2, actor_type="user", actor_id=None, started_at=None,
                tokens=None, supports=[]).status_code == 200
    agent, = rows(api)["events"]
    assert agent == {
        "id": f"{1:032x}", "actor_type": "agent", "actor_id": "legal-assistant", "user_id": "alice",
        "unit": None, "action": "Analyse d’un document", "status": "completed", "source": "reported",
        "started_at": "2026-11-03T08:56:00.000000Z", "finished_at": "2026-11-03T08:58:01.250000Z",
        "duration_ms": 121250, "tokens": {"input": 90, "output": 10, "total": 100},
        "supports": [{"kind": "file", "label": "Rapport A.pdf", "ref": "doc-a"}],
    }
    human, = rows(api, BOB)["events"]
    assert human["actor_id"] == human["user_id"] == "bob"
    assert human["duration_ms"] is human["tokens"] is None
    assert post(api, sequence=3, tokens={"input": 0, "output": 0}).status_code == 200
    assert rows(api)["events"][0]["tokens"] == {"input": 0, "output": 0, "total": 0}
    assert api.get(API, headers=ALICE, params={"user_id": "bob"}).status_code == 422
    snapshot = api.get("/api/v1/snapshot", headers=ALICE).text
    assert "Rapport A.pdf" not in snapshot


def test_admin_all_is_explicit_and_group_and_bu_bound(tmp_path, capsys):
    api = bu_client(tmp_path)
    assert post(api, OPS).status_code == 200
    finance = who("alice", "finance")
    assert rows(api, finance)["events"] == []
    assert rows(api, OPS)["view"] != rows(api, finance)["view"]
    mixed = who("alice", "ops", "finance-edit")
    assert post(api, mixed, sequence=2).status_code == 422
    assert post(api, OPS, sequence=2, unit="cc-finance").status_code == 403
    assert post(api, who("alice", "ops", "finance"), sequence=2, unit="cc-finance").status_code == 403
    assert post(api, mixed, sequence=2, unit="cc-finance").status_code == 200
    assert {row["unit"] for row in rows(api, mixed)["events"]} == {"cc-ops", "cc-finance"}
    assert {row["unit"] for row in rows(api, OPS)["events"]} == {"cc-ops"}
    colleague = who("bob", "ops")
    assert post(api, colleague, sequence=3).status_code == 200
    assert len(rows(api, OPS)["events"]) == 1

    global_admin = who("operator", "global-admin")
    assert rows(api, global_admin)["events"] == []
    assert rows(api, global_admin)["can_view_all"] is True
    expanded = rows(api, global_admin, view="all")
    assert expanded["view"] == rows(api, global_admin)["view"]
    assert {row["user_id"] for row in expanded["events"]} == {"alice", "bob"}
    for headers in (OPS, who("operator", "global-read"),
                    who("operator", "global-read", "unscoped-admin")):
        denied = api.get(API, headers=headers, params={"view": "all"})
        assert denied.status_code == 403 and denied.json() == {"detail": "activity_scope_forbidden"}
    output = capsys.readouterr().err
    audit = [json.loads(line) for line in output.splitlines() if '"event": "activity.global_view"' in line]
    assert [entry["status"] for entry in audit] == [200, 403, 403, 403]
    assert audit[0]["records"] == 3 and len(audit[0]["actor_ref"]) == len(audit[0]["scope_ref"]) == 64
    assert all(value not in output for value in ("Rapport A.pdf", "legal-assistant", "operator@example.org"))


def test_admin_all_without_policy_and_scope_policy_enablement(tmp_path):
    api = proxy(tmp_path)
    assert post(api, sequence=1).status_code == 200
    assert rows(api, ADMIN)["events"] == []
    assert rows(api, ADMIN, view="all")["events"][0]["user_id"] == "alice"
    assert api.get(API, headers=ALICE, params={"view": "all"}).status_code == 403
    scoped = bu_client(tmp_path)
    assert rows(scoped, who("operator", "global-admin"), view="all")["events"] == []
    assert rows(scoped, OPS)["events"] == []


def test_default_admin_role_does_not_grant_global_activity(tmp_path):
    api = proxy(tmp_path, default_role="admin")
    assert post(api).status_code == 200
    unassigned = who("unassigned")
    assert not rows(api, unassigned)["can_view_all"]
    response = api.get(API, headers=unassigned, params={"view": "all"})
    assert response.status_code == 403 and response.json() == {"detail": "activity_scope_forbidden"}
    assert rows(api, ADMIN, view="all")["events"][0]["user_id"] == "alice"


@pytest.mark.parametrize("bad", [
    {"actor_type": "user", "actor_id": "bob"},
    {"actor_id": "https://target.invalid"},
    {"user_id": "foreign-secret"},
    {"source": "workbench"},
    {"action": "C:\\Users\\me\\secret"},
    {"supports": [{"kind": "file", "label": "/secret/file.pdf"}]},
    {"supports": [{"kind": "file", "label": "Note.pdf", "ref": "https://secret.invalid"}]},
    {"supports": [{"kind": "work", "label": "Note.pdf", "raw": "foreign-secret"}]},
    {"supports": [{"kind": "file", "label": "Tab\tSecret"}]},
    {"supports": [{"kind": "file", "label": "Titre\u202e.pdf"}]},
    {"tokens": {"input": True}},
    {"tokens": {"input": 90, "output": 10, "total": 20}},
    {"tokens": {"input": 10**9, "output": 1}},
    {"tokens": {"total": -1}},
    {"started_at": "2026-11-03T08:59:00Z"},
    {"finished_at": "2026-11-03T08:58:01"},
    {"finished_at": "2026-11-03T08:58:01+00:60"},
    {"finished_at": "2026-02-30T00:00:00Z"},
    {"finished_at": "2026-12-03T08:58:01Z"},
])
def test_payload_rejects_raw_and_untrusted_values_without_echo(tmp_path, bad):
    api = proxy(tmp_path)
    denied = post(api, **bad)
    assert denied.status_code == 422
    assert denied.json() == {"detail": "invalid_activity"}
    assert denied.headers["cache-control"] == "no-store"
    assert rows(api)["events"] == []


def test_origin_and_media_controls(tmp_path):
    api = proxy(tmp_path)
    assert post(api, {**ALICE, "Origin": "https://other.invalid"}).status_code == 403
    assert post(api, {**ALICE, "Sec-Fetch-Site": "same-site"}).status_code == 403
    assert api.post(API, headers=ALICE, content=b"{}").status_code == 415
    assert post(api, {**ALICE, "Origin": "https://testserver", "X-Forwarded-Proto": "https"}).status_code == 200


def test_replay_is_atomic_and_never_rewrites_payload(tmp_path):
    api = proxy(tmp_path)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as threads:
        responses = list(threads.map(lambda _: post(api), range(4)))
    assert all(response.status_code == 200 for response in responses)
    assert sorted(response.json()["duplicate"] for response in responses) == [False, True, True, True]
    assert post(api, action="Remplacement interdit").status_code == 409
    assert post(api, started_at="2026-11-03T08:56:00Z", tokens={"input": 90, "output": 10, "total": 100}).json() == {
        "recorded": True, "duplicate": True,
    }
    assert len(rows(api)["events"]) == 1
    if os.name != "nt":
        assert (tmp_path / "state" / "activity.sqlite3").stat().st_mode & 0o077 == 0


def test_window_limit_after_scope_and_bad_queries(tmp_path, monkeypatch):
    api = proxy(tmp_path)
    monkeypatch.setattr(activity, "_LIMIT", 2)
    for number in range(1, 4):
        assert post(api, sequence=number).status_code == 200
    assert post(api, BOB, sequence=9).status_code == 200
    assert post(api, sequence=7, started_at=None, finished_at="2026-10-04T00:00:00Z").status_code == 200
    short = rows(api, days=7)
    assert short["truncated"] and short["since"] == "2026-10-28T00:00:00.000000Z"
    assert [row["id"] for row in short["events"]] == [f"{3:032x}", f"{2:032x}"]
    monkeypatch.setattr(activity, "_LIMIT", 10)
    assert len(rows(api, days=90)["events"]) == 4
    assert len(rows(api, days=30)["events"]) == 3
    assert api.get(API + "?days=7&days=30", headers=ALICE).status_code == 422
    assert api.get(API + "?days=5", headers=ALICE).status_code == 422


def test_workbench_provenance_and_global_partition(tmp_path):
    api = bu_client(tmp_path)
    request = Request({"type": "http", "app": api.app, "headers": []})
    principal = Principal("alice@example.org", frozenset({"viewer"}), True, "proxy",
                          user="alice", groups=("global-read",))
    access = Access(principal, "viewer", READ, "id", "10.0.0.5")
    assert activity.record_workbench_activity(request, access, event(tokens={"input": "unknown"}))
    own = who("alice", "global-read")
    entry, = rows(api, own)["events"]
    assert entry["source"] == "workbench" and entry["unit"] is entry["tokens"] is None
    assert rows(api, OPS)["events"] == []
    assert rows(api, who("operator", "global-admin"), view="all")["events"] == [entry]
    assert post(api, who("alice", "global-edit"), unit="*").status_code == 422
    assert not activity.record_workbench_activity(request, access, event(action="https://raw.invalid"))


def test_storage_failure_is_generic_and_workbench_is_best_effort(tmp_path):
    api = proxy(tmp_path)
    db: Path = api.app.state.activity.path
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"private-corrupt-content")
    assert api.get(API, headers=ALICE).json() == {"detail": "activity_unavailable"}
    assert post(api).json() == {"detail": "activity_unavailable"}
    request = Request({"type": "http", "app": api.app, "headers": []})
    principal = Principal("alice@example.org", frozenset({"viewer"}), True, "proxy",
                          user="alice", groups=("grp-viewer",))
    access = Access(principal, "viewer", READ, "id", "10.0.0.5")
    assert not activity.record_workbench_activity(request, access, event())


def test_read_uses_existing_storage_without_creating_schema(tmp_path):
    api = proxy(tmp_path)
    db: Path = api.app.state.activity.path
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE other (value TEXT)")
    assert rows(api)["events"] == []
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT name FROM sqlite_schema WHERE type = 'table'").fetchall() == [("other",)]
