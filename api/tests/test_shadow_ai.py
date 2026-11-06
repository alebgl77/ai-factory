"""Shadow AI Observatory tests with fictional identifiers and counts."""

from __future__ import annotations

import concurrent.futures
import contextlib
import datetime as dt
import json
import sqlite3
import sys

import pytest

from app.settings import Settings
from app.shadow_ai import main as verify_journal
from tests.conftest import NOW
from tests.test_security import ROLE_MAP, UNTRUSTED_PEER, build, proxy, who

ROUTE = "/api/v1/shadow-ai"
GROUPS = ("obs-viewers", "obs-editors", "obs-admins", "obs-approvers")
SHADOW_MAP = ROLE_MAP + tuple(f"{group}:{role}" for group, role in zip(GROUPS, ("viewer", "editor", "admin", "approver")))
READER = who("obs-reader", "obs-viewers")
EDITOR = who("obs-editor", "obs-editors")
ADMIN = who("obs-admin", "obs-admins")
REVIEWER = who("obs-approver", "obs-approvers")


def live(tmp_path, **kwargs):
    return proxy(tmp_path, shadow_ai_enabled=True, shadow_ai_groups=GROUPS, role_map=SHADOW_MAP, **kwargs)


def usage(team="engineering", tool="external-assistant", people=8, events=35, kind="usage"):
    return {"team": team, "tool": tool, "people": people, "events": events, "kind": kind}


def adoption(team="engineering", tool="internal-assistant", active=11, eligible=20):
    return {"team": team, "tool": tool, "active": active, "eligible": eligible}


def period(*, month="2026-10", team_usage=(), adoption_rows=()):
    return {"period": month, "team_usage": list(team_usage), "adoption": list(adoption_rows)}


def evidence(days_ago=0, users=9):
    end = NOW - dt.timedelta(days=days_ago)
    return {
        "window_start": (end - dt.timedelta(days=30)).isoformat(),
        "window_end": end.isoformat().replace("+00:00", "Z"),
        "events": 160, "unique_users": users,
    }


def propose(client, tool, status, *, alternative=None, revision=0, headers=EDITOR):
    return client.post(f"{ROUTE}/policies/proposals", headers=headers, json={
        "tool": tool, "status": status, "alternative": alternative, "expected_revision": revision,
    })


def decide(client, proposal, *, headers=REVIEWER, decision="approve"):
    proposal_id = proposal.json()["id"]
    return client.post(f"{ROUTE}/policies/proposals/{proposal_id}/decision",
                       headers=headers, json={"decision": decision})


def test_opt_in_proxy_group_and_roles(tmp_path):
    assert build(tmp_path).get(ROUTE).status_code == 404
    assert not (tmp_path / "state" / "shadow-ai").exists()
    with pytest.raises(ValueError, match="proxy authentication"):
        Settings(shadow_ai_enabled=True, shadow_ai_groups=GROUPS)
    with pytest.raises(ValueError, match="GROUPS"):
        Settings(shadow_ai_enabled=True, auth_mode="proxy")
    with pytest.raises(ValueError, match="trusted identity proxy"):
        live(tmp_path, trusted_proxies=("0.0.0.0/0",))
    assert live(tmp_path, peer=UNTRUSTED_PEER).get(ROUTE, headers=ADMIN).status_code == 401
    client = live(tmp_path, default_role="viewer")
    assert client.get(ROUTE).status_code == 401
    assert client.get(ROUTE, headers=who("outsider", "grp-admin")).status_code == 403
    assert client.get(ROUTE, headers=who("outsider")).status_code == 403
    response = client.get(ROUTE, headers=READER)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "privacy_threshold": 5, "evidence": None, "team_usage": [], "adoption": [], "policies": [],
        "proposals": [], "capabilities": {"propose": False, "decide": False},
    }
    assert client.post(f"{ROUTE}/periods", headers=EDITOR, json=period()).status_code == 403
    assert client.post(f"{ROUTE}/evidence", headers=READER, json=evidence()).status_code == 403
    no_stable_id = {"X-Forwarded-Email": "email-only@example.org", "X-Forwarded-Groups": "obs-admins"}
    assert client.post(f"{ROUTE}/periods", headers=no_stable_id, json=period()).status_code == 403
    fallback_admin = live(tmp_path, default_role="admin")
    ordinary_member = who("ordinary", "obs-viewers")
    assert fallback_admin.get(ROUTE, headers=ordinary_member).json()["capabilities"] == {
        "propose": False, "decide": False,
    }
    assert fallback_admin.post(f"{ROUTE}/periods", headers=ordinary_member, json=period()).status_code == 403
    unrelated_admin = who("unrelated-admin", "obs-viewers", "grp-admin", "grp-approver")
    assert client.get(ROUTE, headers=unrelated_admin).json()["capabilities"] == {
        "propose": False, "decide": False,
    }
    assert client.post(f"{ROUTE}/periods", headers=unrelated_admin, json=period()).status_code == 403
    assert client.post(f"{ROUTE}/periods", headers=REVIEWER, json=period()).status_code == 403
    assert client.get(ROUTE, headers=ADMIN).json()["capabilities"] == {"propose": True, "decide": False}
    assert client.get(ROUTE, headers=REVIEWER).json()["capabilities"] == {"propose": False, "decide": True}


def test_secure_request_boundary(tmp_path):
    client = live(tmp_path)
    secret = "do-not-echo-me@example.org"
    response = client.post(f"{ROUTE}/periods", headers=ADMIN, json={**period(), "unexpected": secret})
    assert response.status_code == 422 and response.json() == {"detail": "invalid_request"}
    assert secret not in response.text
    assert client.post(f"{ROUTE}/periods", headers=ADMIN, content=secret).status_code == 415
    assert client.post(f"{ROUTE}/periods", headers=ADMIN, json=period(month="2026-11")).status_code == 422
    assert client.post(f"{ROUTE}/periods", headers=ADMIN, json=period(month="2025-10")).status_code == 422
    cross_site = {**ADMIN, "Sec-Fetch-Site": "cross-site", "Origin": "https://elsewhere.example"}
    assert client.get(ROUTE, headers=cross_site).status_code == 403
    assert client.post(f"{ROUTE}/periods", headers=cross_site, json=period()).status_code == 403
    assert client.post(f"{ROUTE}/periods", headers={**ADMIN, "Origin": "https://elsewhere.example"},
                       json=period()).status_code == 403
    assert client.post(f"{ROUTE}/periods",
                       headers={**ADMIN, "Origin": "https://testserver", "X-Forwarded-Proto": "https"},
                       json=period()).status_code == 200
    response = client.post(f"{ROUTE}/periods", headers={**ADMIN, "Content-Type": "application/json"},
                           content=iter([b"{" * 40_000, b" " * 40_000]))
    assert response.status_code == 413


def test_period_projection_is_immutable_and_never_stores_suppressed_rows(tmp_path):
    client = live(tmp_path)
    open_row = usage()
    small_cohort = usage(team="tiny-cohort", tool="private-tool", people=4)
    duplicate = {**small_cohort, "people": 2}
    for rows in ((small_cohort, duplicate), (usage(people=True),), (usage(people=6, events=5),)):
        assert client.post(f"{ROUTE}/periods", headers=ADMIN, json=period(team_usage=rows)).status_code == 422
    report = period(team_usage=(small_cohort, open_row), adoption_rows=(
        adoption(), adoption(team="tiny-active", active=4), adoption(team="tiny-inactive", active=19),
    ))
    assert client.post(f"{ROUTE}/periods", headers=ADMIN, json=report).json() == {"status": "stored"}
    report["team_usage"].reverse()
    report["team_usage"][1]["people"] = 1
    assert client.post(f"{ROUTE}/periods", headers=ADMIN, json=report).json() == {"status": "stored"}
    report["team_usage"][0] = {**open_row, "people": 9}
    assert client.post(f"{ROUTE}/periods", headers=ADMIN, json=report).status_code == 409
    payload = client.get(ROUTE, headers=READER).json()
    assert payload["team_usage"] == [{"period": "2026-10", **open_row}]
    assert payload["adoption"] == [{"period": "2026-10", **adoption()}]
    path = client.app.state.shadow_ai.path
    assert b"tiny-cohort" not in path.read_bytes()
    assert b"private-tool" not in path.read_bytes()
    assert b"tiny-active" not in path.read_bytes()
    assert b"tiny-inactive" not in path.read_bytes()
    assert "external-assistant" not in client.get("/api/v1/snapshot", headers=READER).text
    assert "external-assistant" not in client.get("/metrics").text
    advanced = live(tmp_path, shadow_ai_privacy_threshold=12)
    assert advanced.get(ROUTE, headers=READER).json()["team_usage"] == []
    assert advanced.get(ROUTE, headers=READER).json()["adoption"] == []
    assert client.get(ROUTE, headers=READER).status_code == 503
    with pytest.raises(ValueError, match="cannot decrease"):
        live(tmp_path)


def test_one_exact_measure_per_team_tool_period(tmp_path):
    client = live(tmp_path)
    same_as_adoption = usage(tool="internal-assistant", people=6)
    adoption_row = adoption(active=5, eligible=10)
    observation = usage(team="platform", people=9, kind="observation")
    selected_usage = usage(team="support", people=7)
    rows = (
        same_as_adoption,
        usage(team="platform", people=4),
        usage(team="platform", people=14, kind="inventory"),
        observation,
        selected_usage,
        usage(team="support", people=10, kind="observation"),
    )
    report = period(team_usage=rows, adoption_rows=(adoption_row,))
    assert client.post(f"{ROUTE}/periods", headers=ADMIN, json=report).status_code == 200
    report["team_usage"].reverse()
    assert client.post(f"{ROUTE}/periods", headers=ADMIN, json=report).status_code == 200
    payload = client.get(ROUTE, headers=READER).json()
    assert payload["team_usage"] == [{"period": "2026-10", **row} for row in (observation, selected_usage)]
    assert payload["adoption"] == [{"period": "2026-10", **adoption_row}]
    with contextlib.closing(sqlite3.connect(client.app.state.shadow_ai.path)) as db:
        stored_usage, stored_adoption = db.execute("SELECT team_usage, adoption FROM periods").fetchone()
        assert json.loads(stored_usage) == [observation, selected_usage]
        assert json.loads(stored_adoption) == [adoption_row]
    raised = live(tmp_path, shadow_ai_privacy_threshold=6).get(ROUTE, headers=READER).json()
    assert raised["adoption"] == []
    assert raised["team_usage"] == payload["team_usage"]


def test_evidence_watermark_expiry_and_safe_validation(tmp_path):
    client = live(tmp_path)
    early, low = evidence(days_ago=1), evidence(users=4)
    assert client.post(f"{ROUTE}/evidence", headers=ADMIN, json=early).status_code == 200
    assert client.post(f"{ROUTE}/evidence", headers=ADMIN, json=early).status_code == 200
    assert client.get(ROUTE, headers=READER).json()["evidence"]["unique_users"] == 9
    assert client.post(f"{ROUTE}/evidence", headers=ADMIN, json=low).status_code == 200
    assert client.get(ROUTE, headers=READER).json()["evidence"] is None
    assert client.post(f"{ROUTE}/evidence", headers=ADMIN, json=early).status_code == 409
    assert client.post(f"{ROUTE}/evidence", headers=ADMIN, json=evidence()).status_code == 409
    for invalid in (evidence(days_ago=8), {**early, "window_end": "2026-11-99T09:00:00Z"},
                    {**early, "unique_users": 9.0}):
        assert client.post(f"{ROUTE}/evidence", headers=ADMIN, json=invalid).status_code == 422
    with contextlib.closing(sqlite3.connect(client.app.state.shadow_ai.path)) as db:
        assert db.execute("SELECT events, unique_users FROM evidence").fetchone() == (None, None)

    recent = {**evidence(), "window_end": "2026-11-03T09:01:00.012345+00:00"}
    assert client.post(f"{ROUTE}/evidence", headers=ADMIN, json=recent).status_code == 200
    assert client.get(ROUTE, headers=READER).json()["evidence"]["window_end"] == recent["window_end"]
    client.app.state.shadow_ai.clock = lambda: NOW + dt.timedelta(days=8)
    assert client.get(ROUTE, headers=READER).json()["evidence"] is None


def test_governance_separation_revision_alternative_and_retry(tmp_path):
    client = live(tmp_path)
    assert propose(client, "some-tool", "reviewing", headers=READER).status_code == 403
    policy = propose(client, "internal-tool", "approved", headers=ADMIN)
    assert policy.status_code == 201
    assert propose(client, "internal-tool", "approved", headers=ADMIN).json() == policy.json()
    assert client.get(ROUTE, headers=READER).json()["proposals"] == []
    queue = client.get(ROUTE, headers=EDITOR).json()["proposals"]
    assert queue == [{"id": policy.json()["id"], "tool": "internal-tool", "status": "approved",
                      "alternative": None, "expected_revision": 0}]
    assert decide(client, policy, headers=ADMIN).status_code == 403
    assert decide(client, policy, headers=who("other", "obs-viewers", "grp-approver")).status_code == 403
    assert decide(client, policy, headers=who("obs-admin", "obs-approvers")).status_code == 403
    assert decide(client, policy, headers=who("renamed", "obs-approvers", email="obs-admin@example.org")).status_code == 403
    assert decide(client, policy, headers=REVIEWER).status_code == 200
    assert decide(client, policy, headers=REVIEWER).status_code == 409
    restricted = propose(client, "external-tool", "restricted", alternative="internal-tool")
    stale = propose(client, "external-tool", "pilot")
    assert decide(client, restricted, headers=EDITOR).status_code == 403
    assert decide(client, restricted).status_code == 200
    assert decide(client, stale).json() == {"detail": "revision_conflict"}
    assert decide(client, stale, decision="reject").status_code == 200
    assert propose(client, "internal-tool", "restricted", revision=1).json() == {"detail": "alternative_in_use"}
    assert propose(client, "other-tool", "reviewing", alternative="external-tool").json() == {
        "detail": "alternative_unavailable",
    }
    chained = propose(client, "other-tool", "approved", alternative="internal-tool")
    assert decide(client, chained).status_code == 200
    assert propose(client, "internal-tool", "approved", revision=1, alternative="other-tool").json() == {
        "detail": "alternative_cycle",
    }
    assert client.get(ROUTE, headers=READER).json()["policies"] == [
        {"tool": "external-tool", "status": "restricted", "alternative": "internal-tool", "revision": 1},
        {"tool": "internal-tool", "status": "approved", "alternative": None, "revision": 1},
        {"tool": "other-tool", "status": "approved", "alternative": "internal-tool", "revision": 1},
    ]


def test_concurrent_decision_and_journal_verification(tmp_path, monkeypatch, capsys):
    client = live(tmp_path)
    first = propose(client, "service", "approved")
    second = propose(client, "service", "restricted")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        status = sorted(pool.map(lambda proposal: decide(client, proposal).status_code, (first, second)))
    assert status == [200, 409]
    path = client.app.state.shadow_ai.path
    monkeypatch.setattr(sys, "argv", ["shadow_ai", "verify", str(path)])
    assert verify_journal() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "verified" and result["events"] == 3 and len(result["head"]) == 64
    with contextlib.closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TRIGGER fail_log BEFORE INSERT ON journal BEGIN SELECT RAISE(ABORT, 'test'); END;")
    assert client.post(f"{ROUTE}/periods", headers=ADMIN, json=period(team_usage=(usage(),))).status_code == 503
    assert client.get(ROUTE, headers=READER).json()["team_usage"] == []
    with contextlib.closing(sqlite3.connect(path)) as db, db:
        db.execute("DELETE FROM journal WHERE seq = 1")
    assert verify_journal() == 1
    assert json.loads(capsys.readouterr().out) == {"status": "unverifiable"}
