"""Factory-wide idea visibility with server-verified author and BU attribution."""

import datetime as dt
import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app import ideas
from app.state import AuditError
from tests.test_bu_access import FINANCE
from tests.test_portfolio import GLOBAL, OPS, OPS_AGENT, OPS_PERSON, REL, REL_AGENT, make_client
from tests.test_security import UNTRUSTED_PEER, audit_lines, build, who


API = "/api/v1/ideas"
FINANCE_BU = "cc-finance"
ADMIN_SOURCE = "@factory-administration"
NOTE = {"bu": OPS, "title": "Revoir les accès SSO", "text": "Documenter les permissions.\nVérifier la conformité."}


def _get(api, headers):
    response = api.get(API + "?lang=fr", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def test_verified_employee_post_is_immediately_visible_across_factory(tmp_path):
    api = make_client(tmp_path)
    assert _get(api, FINANCE)["total"] == 0
    assert not api.app.state.ideas.path.exists()
    response = api.post(API + "?lang=fr", headers=OPS_PERSON, json=NOTE)
    assert response.status_code == 201, response.text
    idea = response.json()
    assert re.fullmatch(r"[0-9a-f]{32}", idea["id"])
    assert {key: idea[key] for key in NOTE} == NOTE
    assert set(idea) == {"id", "bu", "bu_label", "title", "text", "category", "tags", "created_at", "visibility"}
    assert idea["visibility"] == "factory"
    assert idea["category"] == "security"
    assert idea["tags"] == ["access", "compliance"]
    assert idea["created_at"].endswith("Z")
    for member in (FINANCE, REL_AGENT, GLOBAL, who("readonly", "global-read")):
        result = _get(api, member)
        assert result["items"] == [idea]
        assert result["total"] == 1 and result["truncated"] is False
    assert _get(api, FINANCE)["units"][0]["id"] == FINANCE_BU
    assert _get(api, FINANCE)["view"] != _get(api, OPS_PERSON)["view"]


def test_write_bu_is_bound_to_employee_agent_or_global_admin(tmp_path):
    api = make_client(tmp_path)
    split = who("unlinked", "finance-admin", "global-read")
    viewer = who("unlinked", "ops")
    privileged_agent = who("doc-agent-subject", "ops-agent", "global-admin")
    for author, bu in ((OPS_PERSON, FINANCE_BU), (OPS_AGENT, REL), (privileged_agent, REL),
                       (split, FINANCE_BU), (viewer, OPS), (GLOBAL, "cc-forged"),
                       (GLOBAL, FINANCE_BU), (OPS_PERSON, ADMIN_SOURCE), (privileged_agent, ADMIN_SOURCE)):
        assert api.post(API, headers=author, json={**NOTE, "bu": bu}).status_code == 403
    assert not api.app.state.ideas.path.exists()
    assert _get(api, split)["permissions"] == {"create": False, "trace": False}
    assert _get(api, viewer)["units"] == []
    assert {unit["id"] for unit in _get(api, privileged_agent)["units"]} == {OPS}
    assert api.post(API, headers=OPS_AGENT, json=NOTE).status_code == 201
    assert api.post(API, headers=GLOBAL, json={**NOTE, "bu": ADMIN_SOURCE}).status_code == 201
    assert api.post(API, headers=FINANCE, json={**NOTE, "bu": FINANCE_BU}).status_code == 201
    assert {item["bu"] for item in _get(api, viewer)["items"]} == {OPS, FINANCE_BU, ADMIN_SOURCE}


def test_default_source_derives_from_verified_membership_not_note_theme(tmp_path):
    api = make_client(tmp_path)
    note = {"title": "Analyse financière", "text": "Documenter les marges et le budget."}
    for author, bu in ((OPS_PERSON, OPS), (OPS_AGENT, OPS), (FINANCE, FINANCE_BU), (GLOBAL, ADMIN_SOURCE)):
        response = api.post(API + "?lang=fr", headers=author, json=note)
        assert response.status_code == 201, response.text
        card = response.json()
        assert card["bu"] == bu and card["category"] == "finance"
        assert card["tags"] == ["financial_analysis", "budget"]
        if bu == ADMIN_SOURCE:
            assert card["bu_label"] == "Administration Factory"
    data = api.get(API + "?lang=en", headers=GLOBAL).json()
    assert data["units"] == [{"id": ADMIN_SOURCE, "label": "Factory Administration"}]
    assert next(item for item in data["items"] if item["bu"] == ADMIN_SOURCE)["bu_label"] == "Factory Administration"


def test_named_admin_writes_only_for_verified_bu_and_multiple_sources_require_choice(tmp_path):
    api = make_client(tmp_path, people={OPS: ["p01"], FINANCE_BU: ["p01", "p07"]})
    named_admin = who("alice", "global-admin")
    assert {unit["id"] for unit in _get(api, named_admin)["units"]} == {OPS, FINANCE_BU}
    body = {"title": NOTE["title"], "text": NOTE["text"]}
    ambiguous = api.post(API, headers=named_admin, json=body)
    assert ambiguous.status_code == 422 and ambiguous.json() == {"detail": "ideas_bu_required"}
    assert api.post(API, headers=named_admin, json={**body, "bu": ADMIN_SOURCE}).status_code == 403
    assert api.post(API, headers=named_admin, json={**body, "bu": FINANCE_BU}).json()["bu"] == FINANCE_BU
    assert api.post(API, headers=OPS_PERSON, json=body).json()["bu"] == OPS


def test_administration_source_collision_fails_closed(tmp_path, monkeypatch):
    api = make_client(tmp_path)
    config = api.app.state.service.config
    data = deepcopy(config.get())
    data["factory"]["cost_centers"].append({"id": ADMIN_SOURCE, "name": "Configured collision"})
    monkeypatch.setattr(config, "get", lambda: data)
    assert api.get(API, headers=GLOBAL).status_code == 503
    assert api.post(API, headers=GLOBAL, json=NOTE).status_code == 503
    assert not api.app.state.ideas.path.exists()


def test_retired_legacy_bu_does_not_become_transversal_or_use_its_quota(tmp_path, monkeypatch):
    monkeypatch.setattr(ideas, "_BU_CAPACITY", 1)
    api = make_client(tmp_path)
    store = api.app.state.ideas
    legacy_bu = "factory:administration"
    legacy_id = "b" * 32
    store.path.parent.mkdir(exist_ok=True)
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute(ideas._SCHEMA)
        db.execute("INSERT INTO ideas VALUES (?, ?, ?, ?, 'uncategorized', '[]', '2026-11-07T12:00:00Z')",
                   (legacy_id, legacy_bu, "Legacy", "Historical actual unit"))
    assert _get(api, GLOBAL)["total"] == 0
    assert api.post(f"{API}/{legacy_id}/trace", headers=GLOBAL, json={}).status_code == 404
    added = api.post(API, headers=GLOBAL, json={"title": NOTE["title"], "text": NOTE["text"]})
    assert added.status_code == 201 and added.json()["bu"] == ADMIN_SOURCE
    result = _get(api, OPS_PERSON)
    assert result["total"] == 1 and [item["bu"] for item in result["items"]] == [ADMIN_SOURCE]

    config = api.app.state.service.config
    data = deepcopy(config.get())
    data["factory"]["cost_centers"].append({"id": legacy_bu, "name": "Historical actual unit"})
    monkeypatch.setattr(config, "get", lambda: data)
    result = _get(api, OPS_PERSON)
    assert result["total"] == 2
    assert {item["bu"]: item["bu_label"] for item in result["items"]} == {
        ADMIN_SOURCE: "Administration Factory", legacy_bu: "Historical actual unit",
    }


def test_revoked_and_unready_agents_cannot_use_human_grants(tmp_path, monkeypatch):
    api = make_client(tmp_path)
    pending = who("pending-agent-subject", "relation-agent", "global-admin")
    assert api.get(API, headers=pending).status_code == 403
    assert api.post(API, headers=pending, json={**NOTE, "bu": REL}).status_code == 403
    assert api.post(API, headers=OPS_AGENT, json=NOTE).status_code == 201
    config = api.app.state.service.config
    data = deepcopy(config.get())
    next(agent for agent in data["catalog"]["agents"] if agent["id"] == "ag-doc-research")["eval_status"] = "failed"
    monkeypatch.setattr(config, "get", lambda: data)
    privileged_agent = who("doc-agent-subject", "ops-agent", "global-admin")
    assert api.get(API, headers=privileged_agent).status_code == 403
    assert api.post(API, headers=privileged_agent, json=NOTE).status_code == 403
    assert _get(api, FINANCE)["total"] == 1


def test_demo_anonymous_untrusted_and_cross_origin_are_denied_without_state(tmp_path):
    demo = build(tmp_path)
    assert demo.get(API).status_code == 404
    assert demo.post(API, json=NOTE).status_code == 404
    assert demo.post(f"{API}/{'a' * 32}/trace", json={}).status_code == 404
    assert not hasattr(demo.app.state, "ideas")
    api = make_client(tmp_path)
    assert api.get(API).status_code == 401
    assert api.get(API, headers=who("unknown", "unscoped-admin")).status_code == 403
    untrusted = TestClient(api.app, client=UNTRUSTED_PEER)
    assert untrusted.get(API, headers=GLOBAL).status_code == 401
    assert api.get(API, headers={**GLOBAL, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert api.post(API, headers={**GLOBAL, "Origin": "https://other.invalid"}, json=NOTE).status_code == 403
    assert not api.app.state.ideas.path.exists()


@pytest.mark.parametrize("change", [
    {"author": "secret-note"}, {"category": "secret-note"}, {"visibility": "secret-note"},
    {"text": "secret-note" + "x" * 1000}, {"title": "secret-note\nsecond line"},
    {"text": "\tsecret-note"}, {"text": "secret-note\u202e"}, {"text": " "},
    {"title": "x" * 121}, {"text": 123}, {"bu": None},
])
def test_schema_and_errors_never_echo_invalid_content(tmp_path, change):
    api = make_client(tmp_path)
    response = api.post(API, headers=OPS_PERSON, json={**NOTE, **change})
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_ideas_request"}
    assert not api.app.state.ideas.path.exists()


def test_post_json_and_query_contract(tmp_path):
    api = make_client(tmp_path)
    assert api.post(API, headers=GLOBAL, data=NOTE).status_code == 415
    assert api.get(API + "?author=secret-note", headers=GLOBAL).json() == {"detail": "invalid_ideas_request"}
    assert api.get(API + "?lang=fr&lang=en", headers=GLOBAL).status_code == 422
    idea = api.post(API, headers=OPS_PERSON, json={**NOTE, "title": "  Espace café  ", "text": "  À discuter  "}).json()
    assert idea["title"] == "Espace café" and idea["text"] == "À discuter"
    assert api.post(f"{API}/{idea['id']}/publish", headers=GLOBAL, json={}).status_code == 404


@pytest.mark.parametrize(("title", "text", "expected"), [
    ("Réviser les ACCÈS SSO", "Permissions et conformité", ("security", ["access", "compliance"])),
    ("Automate approval workflow", "CRM connector", ("automation", ["workflow", "integration"])),
    ("Analyse financière", "Trésorerie", ("finance", ["financial_analysis", "budget"])),
    ("Financial analysis", "Revenue and costs", ("finance", ["financial_analysis", "budget"])),
    ("Qualité des données", "Analytics et doublons", ("data", ["analytics", "quality"])),
    ("Accessible navigation", "Feedback survey for the interface", ("experience", ["usability", "feedback"])),
    ("Former les équipes au catalogue", "Un guide et des ateliers", ("adoption", ["training", "documentation"])),
    ("Une table plus ronde", "À en reparler", ("uncategorized", [])),
    ("SSO", "workflow workflow workflow", ("security", ["access"])),
    ("Access workflow", "", ("security", ["access"])),
])
def test_local_classifier_is_bilingual_bounded_and_deterministic(title, text, expected):
    assert ideas.classify_idea(title, text) == expected


def test_board_cap_global_total_and_persistence(tmp_path, monkeypatch):
    monkeypatch.setattr(ideas, "_VISIBLE_LIMIT", 1)
    api = make_client(tmp_path)
    for author, bu in ((OPS_PERSON, OPS), (FINANCE, FINANCE_BU)):
        assert api.post(API, headers=author, json={**NOTE, "bu": bu}).status_code == 201
    api = make_client(tmp_path)
    result = _get(api, OPS_PERSON)
    assert len(result["items"]) == 1 and result["total"] == 2 and result["truncated"] is True
    with closing(sqlite3.connect(api.app.state.ideas.path)) as db, db:
        db.execute("UPDATE ideas SET bu = 'cc-retired' WHERE bu = ?", (FINANCE_BU,))
    result = _get(api, FINANCE)
    assert len(result["items"]) == 1 and result["total"] == 1 and result["truncated"] is False


def test_search_and_page_span_history_without_leaking_retired_rows_or_provenance(tmp_path):
    api = make_client(tmp_path)
    clock = lambda: dt.datetime(2026, 11, 7, 12, tzinfo=dt.UTC)
    api.app.state.ideas.clock = clock
    titles = ["Audit ancien", "Audit récent", "Audit finances", "Autre sujet"]
    posts = [
        api.post(API, headers=actor, json={**NOTE, "bu": bu, "title": title}).json()
        for title, (actor, bu) in zip(titles, ((OPS_PERSON, OPS), (OPS_PERSON, OPS), (FINANCE, FINANCE_BU),
                                               (OPS_PERSON, OPS)), strict=True)
    ]
    params = {"q": "AUDIT", "category": "security", "limit": 1}
    first = api.get(API, headers=FINANCE, params=params).json()
    assert first["total"] == 3 and first["truncated"]
    assert first["category_totals"] == {"security": 3}
    assert first["items"][0]["id"] == min(row["id"] for row in posts[:3])
    cursor = first["next_cursor"]
    # A same-clock arrival after the first page is excluded from this traversal.
    api.post(API, headers=OPS_PERSON, json={**NOTE, "title": "Audit nouveau"})
    api = make_client(tmp_path)
    items = first["items"]
    while cursor:
        response = api.get(API, headers=FINANCE, params={**params, "cursor": cursor})
        assert response.status_code == 200, response.text
        payload = response.json()
        items.extend(payload["items"])
        cursor = payload["next_cursor"]
    assert [row["id"] for row in items] == sorted(row["id"] for row in posts[:3])
    assert all({"subject", "kind"}.isdisjoint(row) for row in items)
    with closing(sqlite3.connect(api.app.state.ideas.path)) as db, db:
        db.execute("UPDATE ideas SET bu = 'cc-retired' WHERE bu = ?", (FINANCE_BU,))
    scoped = api.get(API, headers=FINANCE, params={**params, "bu": OPS, "limit": 120}).json()
    assert {row["title"] for row in scoped["items"]} == {"Audit ancien", "Audit récent", "Audit nouveau"}
    assert api.get(API, headers=FINANCE, params={"q": "récent"}).json()["items"][0]["id"] == posts[1]["id"]
    assert api.get(API, headers=FINANCE, params={"q": "FINANCES"}).json()["total"] == 0


def test_search_rejects_cursor_reuse_and_treats_sql_operators_as_text(tmp_path):
    api = make_client(tmp_path)
    for _ in range(2):
        assert api.post(API, headers=OPS_PERSON, json=NOTE).status_code == 201
    params = {"limit": 1, "q": "sso", "lang": "fr"}
    cursor = api.get(API, headers=FINANCE, params=params).json()["next_cursor"]
    for actor, change in (
        (FINANCE, {"q": "other"}), (FINANCE, {"lang": "en"}), (FINANCE, {"category": "security"}),
        (FINANCE, {"bu": OPS}), (FINANCE, {"limit": 2}), (OPS_PERSON, {}),
        (FINANCE, {"cursor": ("b" if cursor[0] == "a" else "a") + cursor[1:]}),
    ):
        result = api.get(API, headers=actor, params={**params, "cursor": cursor, **change})
        assert result.status_code == 422 and result.json() == {"detail": "invalid_ideas_cursor"}
    injection = api.get(API, headers=FINANCE, params={"q": "%' OR 1=1 --"})
    assert injection.status_code == 200 and injection.json()["items"] == []
    assert api.get(API + "?limit=1&limit=2", headers=FINANCE).status_code == 422
    assert api.get(API, headers=FINANCE, params={"bu": "cc-retired"}).status_code == 422
    assert api.get(API, headers=FINANCE, params={"category": "private"}).status_code == 422
    assert api.post(API + "?q=sso", headers=OPS_PERSON, json=NOTE).status_code == 422


def test_capacity_is_transactional_for_concurrent_writers(tmp_path, monkeypatch):
    monkeypatch.setattr(ideas, "_BU_CAPACITY", 1)
    store = ideas.IdeasStore(tmp_path)
    body = ideas.IdeaIn(**NOTE)

    def submit():
        try:
            store.create(body, bu=OPS, subject="employee-fixture", kind="employee")
            return True
        except ideas.IdeasCapacity:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [executor.submit(submit) for _ in range(2)]
        assert sum(result.result() for result in outcomes) == 1
    _, total = store.list(frozenset({OPS}))
    assert total == 1


def test_full_bu_returns_bounded_error_without_blocking_other_bu(tmp_path, monkeypatch):
    monkeypatch.setattr(ideas, "_BU_CAPACITY", 1)
    api = make_client(tmp_path)
    assert api.post(API, headers=OPS_PERSON, json=NOTE).status_code == 201
    full = api.post(API, headers=OPS_PERSON, json={**NOTE, "text": "secret-note"})
    assert full.status_code == 409 and full.json() == {"detail": "ideas_capacity_reached"}
    assert api.post(API, headers=FINANCE, json={**NOTE, "bu": FINANCE_BU}).status_code == 201


def test_trace_is_separate_exact_no_store_and_audited_for_each_author_kind(tmp_path, capsys):
    api = make_client(tmp_path)
    tracer = who("audit-requester", "global-admin", "global-read")
    assert _get(api, tracer)["permissions"]["trace"] is True
    assert _get(api, OPS_PERSON)["permissions"]["trace"] is False
    expected = ((OPS_PERSON, "alice", "employee", OPS), (OPS_AGENT, "doc-agent-subject", "agent", OPS),
                (GLOBAL, "direction", "admin", ADMIN_SOURCE))
    for author, subject, kind, bu in expected:
        card = api.post(API + "?lang=fr", headers=author, json={**NOTE, "bu": bu}).json()
        assert {"subject", "kind"}.isdisjoint(card)
        assert all({"subject", "kind"}.isdisjoint(item) for item in _get(api, tracer)["items"])
        response = api.post(f"{API}/{card['id']}/trace?lang=fr", headers=tracer, json={})
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {"id": card["id"], "bu": bu, "subject": subject, "kind": kind,
                                   "created_at": card["created_at"], "view": _get(api, tracer)["view"]}
        record = audit_lines(tmp_path)[-1]
        assert record["action"] == "ideas.author_trace" and record["target"] == f"idea:{card['id']}"
        assert record["actor"] == "audit-requester@example.org" and record["role"] == "admin"
        assert record["details"] == {} and record["before_hash"] is None and record["after_hash"] is None
        assert subject not in json.dumps(record) and NOTE["title"] not in json.dumps(record)
    audit = api.get("/api/v1/audit?action=ideas.author_trace", headers=tracer)
    assert audit.status_code == 200 and audit.json()["total"] == len(expected)
    logs = capsys.readouterr().err
    assert "ideas.author_trace" in logs
    assert all(subject not in logs for _, subject, _, _ in expected)
    assert NOTE["title"] not in logs


def test_trace_denies_before_any_lookup_and_uses_strict_proxy_contract(tmp_path, monkeypatch):
    api = make_client(tmp_path)

    def unexpected_lookup(*_args):
        pytest.fail("trace storage read before authorization")

    monkeypatch.setattr(api.app.state.ideas, "origin", unexpected_lookup)
    path = f"{API}/{'a' * 32}/trace"
    accidental_admin_agent = who("doc-agent-subject", "ops-agent", "global-admin")
    split_admin = who("split", "finance-admin", "global-read")
    for reader in (OPS_PERSON, OPS_AGENT, FINANCE, accidental_admin_agent, split_admin):
        assert _get(api, reader)["permissions"]["trace"] is False
        assert api.post(path, headers=reader, json={}).status_code == 403
    assert api.post(path, json={}).status_code == 401
    assert TestClient(api.app, client=UNTRUSTED_PEER).post(path, headers=GLOBAL, json={}).status_code == 401
    assert api.post(path, headers={**GLOBAL, "Origin": "https://other.invalid"}, json={}).status_code == 403
    malformed = api.post(path, headers=GLOBAL, json={"subject": "do-not-echo"})
    assert malformed.status_code == 422 and malformed.json() == {"detail": "invalid_ideas_request"}
    assert api.post(f"{API}/bad-idea-id/trace", headers=GLOBAL, json={}).status_code == 422
    assert api.post(path, headers=GLOBAL, data={"subject": "do-not-echo"}).status_code == 415
    assert audit_lines(tmp_path) == [] and not api.app.state.ideas.path.exists()


def test_legacy_origin_unavailable_without_bootstrap_or_backfill(tmp_path):
    api = make_client(tmp_path)
    idea_id = "a" * 32
    path = f"{API}/{idea_id}/trace"
    missing = api.post(path, headers=GLOBAL, json={})
    assert missing.status_code == 404 and missing.json() == {"detail": "idea_origin_unavailable"}
    store = api.app.state.ideas
    assert not store.path.exists()
    store.path.parent.mkdir(exist_ok=True)
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute(ideas._SCHEMA)
        db.execute("INSERT INTO ideas VALUES (?, ?, ?, ?, 'uncategorized', '[]', '2026-11-07T12:00:00Z')",
                   (idea_id, OPS, "Legacy", "No historical origin"))
    assert _get(api, FINANCE)["items"][0]["id"] == idea_id
    assert api.post(path, headers=GLOBAL, json={}).json() == missing.json()
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT 1 FROM sqlite_schema WHERE name='idea_origins'").fetchone() is None
    assert api.post(API, headers=OPS_PERSON, json=NOTE).status_code == 201
    assert api.post(path, headers=GLOBAL, json={}).json() == missing.json()
    assert audit_lines(tmp_path) == []


def test_trace_fails_closed_if_audit_cannot_commit(tmp_path, monkeypatch):
    api = make_client(tmp_path)
    card = api.post(API, headers=OPS_PERSON, json=NOTE).json()
    state = api.app.state.service.state

    def fail_audit(*_args, **_kwargs):
        raise AuditError("sensitive failing storage diagnostic")

    monkeypatch.setattr(state, "append_audit", fail_audit)
    response = api.post(f"{API}/{card['id']}/trace", headers=GLOBAL, json={})
    assert response.status_code == 503 and response.json() == {"detail": "ideas_trace_unavailable"}
    assert audit_lines(tmp_path) == []


def test_trace_fails_closed_on_a_corrupt_prior_audit_record(tmp_path):
    api = make_client(tmp_path)
    card = api.post(API, headers=OPS_PERSON, json=NOTE).json()
    trace_path = f"{API}/{card['id']}/trace"
    assert api.post(trace_path, headers=GLOBAL, json={}).status_code == 200
    audit_path = api.app.state.service.state.audit.path
    corrupted = audit_path.read_bytes().replace(b'"role":"admin"', b'"role":"tampered"', 1)
    audit_path.write_bytes(corrupted)
    response = api.post(trace_path, headers=GLOBAL, json={})
    assert response.status_code == 503 and response.json() == {"detail": "ideas_trace_unavailable"}
    assert audit_path.read_bytes() == corrupted
    assert len(audit_lines(tmp_path)) == 1


def test_origin_insert_failure_rolls_back_idea(tmp_path):
    store = ideas.IdeasStore(tmp_path)
    body = ideas.IdeaIn(**NOTE)
    with pytest.raises(sqlite3.IntegrityError):
        store.create(body, bu=OPS, subject="fixture", kind="invalid")
    assert store.list(frozenset({OPS})) == ([], 0)
