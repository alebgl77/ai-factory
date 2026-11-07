"""Factory-wide idea visibility with server-verified author and BU attribution."""

import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app import ideas
from tests.test_bu_access import FINANCE
from tests.test_portfolio import GLOBAL, OPS, OPS_AGENT, OPS_PERSON, REL, REL_AGENT, make_client
from tests.test_security import UNTRUSTED_PEER, build, who


API = "/api/v1/ideas"
FINANCE_BU = "cc-finance"
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
                       (split, FINANCE_BU), (viewer, OPS), (GLOBAL, "cc-forged")):
        assert api.post(API, headers=author, json={**NOTE, "bu": bu}).status_code == 403
    assert not api.app.state.ideas.path.exists()
    assert _get(api, split)["permissions"] == {"create": False}
    assert _get(api, viewer)["units"] == []
    assert {unit["id"] for unit in _get(api, privileged_agent)["units"]} == {OPS}
    assert api.post(API, headers=OPS_AGENT, json=NOTE).status_code == 201
    assert api.post(API, headers=GLOBAL, json={**NOTE, "bu": FINANCE_BU}).status_code == 201
    assert api.post(API, headers=FINANCE, json={**NOTE, "bu": FINANCE_BU}).status_code == 201
    assert {item["bu"] for item in _get(api, viewer)["items"]} == {OPS, FINANCE_BU}


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
    {"title": "x" * 121}, {"text": 123},
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
    for bu in (OPS, FINANCE_BU):
        assert api.post(API, headers=GLOBAL, json={**NOTE, "bu": bu}).status_code == 201
    api = make_client(tmp_path)
    result = _get(api, OPS_PERSON)
    assert len(result["items"]) == 1 and result["total"] == 2 and result["truncated"] is True
    with closing(sqlite3.connect(api.app.state.ideas.path)) as db, db:
        db.execute("UPDATE ideas SET bu = 'cc-retired' WHERE bu = ?", (FINANCE_BU,))
    result = _get(api, FINANCE)
    assert len(result["items"]) == 1 and result["total"] == 1 and result["truncated"] is False


def test_capacity_is_transactional_for_concurrent_writers(tmp_path, monkeypatch):
    monkeypatch.setattr(ideas, "_BU_CAPACITY", 1)
    store = ideas.IdeasStore(tmp_path)
    body = ideas.IdeaIn(**NOTE)

    def submit():
        try:
            store.create(body)
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
    assert api.post(API, headers=GLOBAL, json={**NOTE, "bu": FINANCE_BU}).status_code == 201
