"""Portfolio authorization against real verified proxy principals and BU configuration."""

from copy import deepcopy

import pytest

from tests.test_bu_access import CONFIG, FINANCE, ROLE_MAP, policy_file
from tests.test_security import UNTRUSTED_PEER, build, proxy, who

PORTFOLIO = "/api/v1/portfolio"
GLOBAL = who("direction", "global-admin")
OPS_LEAD = who("lead", "ops-lead")
OPS_MANAGER = who("manager", "ops-manager")
OPS_ARCHITECT = who("architect", "ops-architect")
OPS_PERSON = who("alice", "ops")
OPS_AGENT = who("doc-agent-subject", "ops-agent")
REL_LEAD = who("relation-lead", "relation-lead")
REL_MANAGER = who("relation-manager", "relation-manager")
REL_ARCHITECT = who("relation-architect", "relation-architect")
REL_AGENT = who("support-agent-subject", "relation-agent")
REL_RESEARCH = who("research-agent-subject", "relation-agent")
OPS = "cc-ops"
REL = "cc-relation"
GROUPS = {
    "ops": [OPS], "finance": ["cc-finance"], "finance-edit": ["cc-finance"],
    "global-read": ["*"], "global-admin": ["*"], "finance-admin": ["cc-finance"],
    **{f"{prefix}-{role}": [unit] for prefix, unit in [("ops", OPS), ("relation", REL)]
       for role in ("lead", "manager", "architect", "agent")},
}
PORTFOLIO_ROLES = {
    f"{prefix}-{suffix}": [role]
    for prefix in ("ops", "relation")
    for suffix, role in [("lead", "project_lead"), ("manager", "manager"), ("architect", "architect")]
}
BINDINGS = {"doc-agent-subject": "ag-doc-research", "support-agent-subject": "ag-support-supervisor",
            "pending-agent-subject": "ag-support-writer", "research-agent-subject": "ag-support-research",
            "triage-agent-subject": "ag-triage", "marketing-agent-subject": "ag-marketing-writer"}
AUTONOMY = {OPS: {"read_only": "autonomous"}, REL: {"prepare": "blocked", "sensitive": "autonomous"}}


def make_client(tmp_path, **policy):
    path = policy_file(tmp_path, **{"groups": GROUPS, "portfolio_roles": PORTFOLIO_ROLES,
                                   "portfolio_agents": BINDINGS, "portfolio_autonomy": AUTONOMY, **policy})
    roles = ROLE_MAP + ("finance-admin:admin",) + tuple(f"{group}:viewer" for group in GROUPS
                                                       if group.startswith(("ops-", "relation-")))
    return proxy(tmp_path, config_dir=str(CONFIG), role_map=roles, bu_policy_path=path,
                 workbench_mode="protected_demo")


def identifier(value):
    return f"{value:032x}"


def post(api, path, headers, body, status=201):
    response = api.post(PORTFOLIO + path, headers=headers, json=body)
    assert response.status_code == status, response.text
    return response.json()


def snapshot(api, headers):
    response = api.get(PORTFOLIO, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def project(api, index=1, unit=OPS, *, title="Objective"):
    goal_id = identifier(index)
    project_id = identifier(index + 1)
    post(api, "/goals", GLOBAL, {"id": goal_id, "title": title, "metric": "Manual steps",
                                 "target": 0, "current": None, "direction": "at_most"})
    post(api, "/projects", GLOBAL, {"id": project_id, "goal_id": goal_id, "title": "Delivery",
                                    "owner_unit": unit, "unit_ids": [unit], "state": "active",
                                    "usecase_id": "uc-01" if unit == OPS else "uc-02"})
    return project_id


def task(api, index, project_id, unit=OPS, **fields):
    actor = OPS_LEAD if unit == OPS else REL_LEAD
    return post(api, "/tasks", actor, {"project_id": project_id, "unit": unit, "title": f"Task {index}", **fields})


def command(api, row, actor, action, status=200, **fields):
    return post(api, f"/tasks/{row['id']}/commands", actor,
                {"revision": row["revision"], "action": action, **fields}, status)


def assign(api, row, actor, kind, assignee, status=200):
    return command(api, row, actor, "assign", status, assignee={"kind": kind, "id": assignee})


def delegate(api, row, actor, index, status=201, *, mode="automatic", **fields):
    return post(api, f"/tasks/{row['id']}/subagents", actor,
                {"revision": row["revision"], "mode": mode, "title": f"Delegated task {index}", **fields}, status)


def test_proxy_scope_and_validation_fail_closed(tmp_path):
    assert build(tmp_path).get(PORTFOLIO).status_code == 404
    api = make_client(tmp_path)
    assert api.get(PORTFOLIO).status_code == 401
    untrusted = proxy(tmp_path, UNTRUSTED_PEER, config_dir=str(CONFIG),
                      bu_policy_path=None, demo_mode="always")
    assert untrusted.get(PORTFOLIO, headers=GLOBAL).status_code == 401
    assert api.get(PORTFOLIO, headers={"X-Forwarded-Email": "identity@example.org",
                                      "X-Forwarded-Groups": "global-admin"}).status_code == 403
    assert snapshot(api, OPS_PERSON)["tasks"] == []
    assert not api.app.state.portfolio.path.exists()
    assert api.get(PORTFOLIO, headers={**GLOBAL, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert api.get(PORTFOLIO, headers={**GLOBAL, "Origin": "https://other.example.org"}).status_code == 403
    response = api.post(PORTFOLIO + "/goals", headers=GLOBAL, data={"credential": "never-echo"})
    assert response.status_code == 415 and "never-echo" not in response.text
    response = api.post(PORTFOLIO + "/goals", headers=GLOBAL, json={"credential": "never-echo"})
    assert response.status_code == 422 and "never-echo" not in response.text
    assert api.get(PORTFOLIO + "?user=alice", headers=GLOBAL).status_code == 422
    assert api.get(PORTFOLIO + "?lang=fr&lang=en", headers=GLOBAL).status_code == 422


def test_strategic_summary_drops_ids_and_metadata_outside_participating_units(tmp_path):
    api = make_client(tmp_path)
    goal_id, project_id = identifier(81), identifier(82)
    goal = post(api, "/goals", GLOBAL, {
        "id": goal_id, "title": "Reduce handling time", "description": "PRIVATE_GOAL", "due_on": "2026-12-31",
        "metric": "PRIVATE_METRIC", "target": 23, "current": 24, "direction": "at_least",
    })
    project_row = post(api, "/projects", GLOBAL, {
        "id": project_id, "goal_id": goal_id, "title": "Improve handover", "description": "PRIVATE_PROJECT",
        "owner_unit": OPS, "unit_ids": [OPS, REL], "state": "active", "usecase_id": "uc-01",
    })
    task(api, 80, project_id, description="PRIVATE_TASK")
    for reader in (FINANCE, who("readonly", "global-read")):
        for params in ({}, {"limit": 1}):
            response = api.get(PORTFOLIO, headers=reader, params=params)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["goals"] == [{"title": goal["title"], "status": "met"}]
            assert body["projects"] == [{"title": project_row["title"], "state": "active"}]
            assert body["tasks"] == []
            assert all(value not in response.text for value in
                       ("PRIVATE_", goal_id, project_id, "uc-01", OPS, REL, "2026-12-31"))
        denied = api.put(f"{PORTFOLIO}/projects/{project_id}", headers=reader,
                         json={"revision": 1, "description": "Probe"})
        assert denied.status_code == 403
    for reader in (GLOBAL, OPS_PERSON, REL_LEAD):
        body = snapshot(api, reader)
        assert body["goals"] == [goal] and body["projects"] == [project_row]


def test_task_cursor_survives_restart_and_updates_and_keeps_agent_context(tmp_path):
    api = make_client(tmp_path)
    first_project = project(api, 81)
    second_project = project(api, 85)
    foreign_project = project(api, 91, REL)
    own = [task(api, index, project_id, execution="read_only")
           for index, project_id in ((83, first_project), (87, second_project), (88, second_project))]
    for row in own:
        assign(api, row, OPS_MANAGER, "agent", "ag-doc-research")
    task(api, 93, foreign_project, REL)
    task(api, 84, first_project)
    params = {"limit": 1}
    first = api.get(PORTFOLIO, headers=OPS_AGENT, params=params).json()
    assert [row["id"] for row in first["tasks"]] == [own[2]["id"]]
    assert {row["id"] for row in first["projects"]} == {first_project, second_project}
    assert len(first["goals"]) == 2 and first["truncated"]
    cursor = first["next_cursor"]
    assert api.get(PORTFOLIO, headers=OPS_PERSON, params={**params, "cursor": cursor}).json() == {
        "detail": "invalid_portfolio_cursor"}
    for changed in ({"limit": 2, "cursor": cursor}, {"cursor": cursor},
                    {"limit": 1, "cursor": ("b" if cursor[0] == "a" else "a") + cursor[1:]}):
        response = api.get(PORTFOLIO, headers=OPS_AGENT, params=changed)
        assert response.status_code == 422 and response.json() == {"detail": "invalid_portfolio_cursor"}
    assert api.get(PORTFOLIO + "?limit=1&limit=2", headers=OPS_AGENT).status_code == 422
    # Both mutation and insertion occur ahead of the boundary by update time.
    row = next(item for item in snapshot(api, OPS_AGENT)["tasks"] if item["id"] == own[0]["id"])
    command(api, row, OPS_AGENT, "start")
    added = task(api, 89, second_project, execution="read_only")
    assign(api, added, OPS_MANAGER, "agent", "ag-doc-research")
    api = make_client(tmp_path)
    for expected in reversed(own[:2]):
        response = api.get(PORTFOLIO, headers=OPS_AGENT, params={**params, "cursor": cursor})
        assert response.status_code == 200, response.text
        body = response.json()
        assert [row["id"] for row in body["tasks"]] == [expected["id"]]
        assert {row["id"] for row in body["projects"]} == {first_project, second_project}
        cursor = body["next_cursor"]
    assert cursor is None and body["tasks"][0]["history"][0]["action"] == "start"


@pytest.mark.parametrize("invalid", [
    {"portfolio_roles": {"ops-lead": ["owner"]}},
    {"portfolio_roles": {"undeclared-group": ["manager"]}},
    {"portfolio_agents": {"alice": "ag-doc-research"}},
    {"portfolio_agents": {"unregistered-agent": "unknown"}},
    {"portfolio_agents": {"first": "ag-doc-research", "second": "ag-doc-research"}},
    {"portfolio_autonomy": {"cc-unknown": {"prepare": "autonomous"}}},
    {"portfolio_autonomy": {OPS: {"write": "autonomous"}}},
    {"portfolio_autonomy": {OPS: {"prepare": "unchecked"}}},
])
def test_invalid_portfolio_policy_rejected_at_boot(tmp_path, invalid):
    with pytest.raises(ValueError, match="invalid FACTORY_BU_POLICY"):
        make_client(tmp_path, **invalid)


def test_exact_roles_own_tasks_and_revisions(tmp_path):
    api = make_client(tmp_path)
    forged_admin = who("financier", "global-read", "finance-admin")
    post(api, "/goals", forged_admin,
         {"id": identifier(99), "title": "Forbidden", "metric": "Steps", "target": 1}, 403)
    project_id = project(api)
    other = project(api, 10, REL)
    forbidden = {"project_id": other, "unit": REL, "title": "Cross-unit task"}
    post(api, "/tasks", who("mix", "ops-lead", "relation-agent"), forbidden, 403)
    post(api, "/tasks", GLOBAL, forbidden, 403)
    post(api, "/projects", GLOBAL,
         {"id": identifier(19), "goal_id": identifier(1), "title": "Wrong use case",
          "owner_unit": OPS, "unit_ids": [OPS], "usecase_id": "uc-02"}, 422)
    first = task(api, 21, project_id, technical=True)
    teammate = task(api, 22, project_id)
    foreign = task(api, 23, other, REL, description="private cross-BU task")
    assert len({first["id"], teammate["id"], foreign["id"]}) == 3
    forged = {"project_id": project_id, "unit": OPS, "title": "Probe"}
    assert post(api, "/tasks", OPS_LEAD, {**forged, "id": foreign["id"]}, 422) == (
        post(api, "/tasks", OPS_LEAD, {**forged, "id": identifier(24)}, 422))
    assign(api, first, GLOBAL, "employee", "p01", 403)
    assign(api, first, OPS_MANAGER, "employee", "p07", 422)
    assert command(api, foreign, OPS_MANAGER, "assign", 404, assignee={"kind": "employee", "id": "p01"}) == (
        command(api, {**foreign, "id": identifier(24)}, OPS_MANAGER, "assign", 404,
                assignee={"kind": "employee", "id": "p01"}))
    first = assign(api, first, OPS_MANAGER, "employee", "p01")
    assign(api, teammate, OPS_MANAGER, "employee", "p02")
    assert {row["id"] for row in snapshot(api, OPS_ARCHITECT)["tasks"]} == {first["id"], teammate["id"]}
    employee_view = snapshot(api, OPS_PERSON)
    assert [row["id"] for row in employee_view["tasks"]] == [first["id"]]
    assert [row["id"] for row in employee_view["people"]] == ["p01"]
    assert len(employee_view["projects"]) == 2 and "private cross-BU task" not in str(employee_view)
    assert employee_view["view"] != snapshot(api, OPS_MANAGER)["view"]
    assert len(snapshot(api, GLOBAL)["tasks"]) == 3
    command(api, first, OPS_MANAGER, "start", 403)
    command(api, {**first, "revision": 1}, OPS_PERSON, "start", 409)
    first = command(api, first, OPS_PERSON, "start")
    command(api, first, OPS_PERSON, "submit", 403, tokens={"input": 1, "output": 1})
    command(api, first, OPS_PERSON, "report_usage", 403, tokens={"input": 1, "output": 1})
    first = command(api, first, OPS_PERSON, "submit", evidence=[{"kind": "work", "ref": "E-1", "label": "QA result"}])
    assert first["status"] == "review"
    command(api, first, who("alice", "ops", "ops-architect"), "approve", 403)
    first = command(api, first, OPS_ARCHITECT, "approve")
    assert first["status"] == "done" and first["evidence"][0]["ref"] == "E-1"
    assert all("actor_ref" not in item and "alice" not in item["actor"] for item in first["history"])
    assert api.get("/api/v1/workbench/cases", headers=OPS_ARCHITECT).status_code == 403


def test_agent_binding_autonomy_and_policy_restart(tmp_path):
    api = make_client(tmp_path)
    ops_project = project(api)
    relation_project = project(api, 10, REL, title="Separate objective")
    prepared = task(api, 21, ops_project, execution="prepare")
    blocked = task(api, 22, relation_project, REL, execution="prepare")
    assert assign(api, blocked, REL_MANAGER, "agent", "ag-support-supervisor", 403)["detail"] == "portfolio_agent_blocked"
    assert assign(api, prepared, OPS_MANAGER, "agent", "ag-support-supervisor", 422)["detail"] == "portfolio_assignee_unavailable"
    prepared = assign(api, prepared, OPS_MANAGER, "agent", "ag-doc-research")
    command(api, prepared, OPS_AGENT, "start", 403)
    command(api, prepared, who("fake-agent", "ops-agent"), "start", 404)
    command(api, prepared, REL_AGENT, "start", 404)
    command(api, prepared, OPS_LEAD, "authorize_start", 403)
    prepared = command(api, prepared, OPS_MANAGER, "authorize_start")
    assert prepared["start_authorized"] is True and prepared["autonomy"] == "human_review"
    prepared = assign(api, prepared, OPS_MANAGER, "agent", "ag-doc-research")
    assert prepared["start_authorized"] is False
    command(api, prepared, OPS_AGENT, "start", 403)
    prepared = command(api, prepared, OPS_MANAGER, "authorize_start")
    granted_view = snapshot(api, OPS_AGENT)["view"]
    original_fingerprint = api.app.state.service.bu_policy.fingerprint

    # Startup loads a new trusted policy. Even an unrelated policy edit invalidates an old grant.
    api = make_client(tmp_path, portfolio_autonomy={**AUTONOMY, REL: {**AUTONOMY[REL], "read_only": "autonomous"}})
    merged_groups = who("doc-agent-subject", "ops-agent", "global-admin", "relation-manager")
    agent_view = snapshot(api, merged_groups)
    assert agent_view["view"] != granted_view
    assert [row["id"] for row in agent_view["projects"]] == [ops_project]
    assert len(agent_view["goals"]) == 1 and agent_view["people"] == []
    assert [row["id"] for row in agent_view["tasks"]] == [prepared["id"]]
    assert agent_view["tasks"][0]["start_authorized"] is False
    assert agent_view["capabilities"]["roles_by_unit"] == {} and not agent_view["capabilities"]["global_admin"]
    command(api, prepared, merged_groups, "start", 403)

    # Restoring byte-identical policy and reconstructing with the same state
    # cannot revive that original grant.
    api = make_client(tmp_path)
    assert api.app.state.service.bu_policy.fingerprint == original_fingerprint
    assert snapshot(api, OPS_AGENT)["tasks"][0]["start_authorized"] is False
    command(api, prepared, OPS_AGENT, "start", 403)
    prepared = command(api, prepared, OPS_MANAGER, "authorize_start")
    assert snapshot(api, OPS_AGENT)["tasks"][0]["start_authorized"] is True
    api = make_client(tmp_path)
    assert api.app.state.service.bu_policy.fingerprint == original_fingerprint
    assert snapshot(api, OPS_AGENT)["tasks"][0]["start_authorized"] is False
    command(api, prepared, OPS_AGENT, "start", 403)
    prepared = command(api, prepared, OPS_MANAGER, "authorize_start")
    prepared = command(api, prepared, OPS_AGENT, "start")
    assert prepared["start_authorized"] is False and prepared["status"] == "in_progress"
    prepared = command(api, prepared, OPS_AGENT, "block")
    prepared = command(api, prepared, OPS_AGENT, "unblock")
    command(api, prepared, OPS_AGENT, "start", 403)

    autonomous = task(api, 23, ops_project, execution="read_only")
    autonomous = assign(api, autonomous, OPS_MANAGER, "agent", "ag-doc-research")
    command(api, autonomous, OPS_MANAGER, "authorize_start", 403)
    autonomous = command(api, autonomous, OPS_AGENT, "start")
    assert command(api, autonomous, OPS_AGENT, "submit")["status"] == "done"

    sensitive = task(api, 24, relation_project, REL, execution="sensitive", technical=True)
    assign(api, sensitive, REL_MANAGER, "agent", "ag-support-writer", 422)
    sensitive = assign(api, sensitive, REL_MANAGER, "agent", "ag-support-supervisor")
    sensitive = command(api, sensitive, REL_AGENT, "start")
    assert delegate(api, sensitive, REL_MANAGER, 25, 403)["detail"] == "portfolio_agent_blocked"
    sensitive = command(api, sensitive, REL_AGENT, "submit")
    assert sensitive["status"] == "review"
    sensitive = command(api, sensitive, REL_ARCHITECT, "approve")
    assert sensitive["status"] == "workbench"
    command(api, sensitive, REL_AGENT, "submit", 409)
    assert api.get("/api/v1/workbench/cases", headers=REL_AGENT).status_code == 403


def test_revoked_agent_loses_task_scope_and_manager_can_remediate(tmp_path, monkeypatch):
    api = make_client(tmp_path, portfolio_autonomy={REL: {"read_only": "autonomous"}})
    active = task(api, 20, project(api, 10, REL), REL, execution="read_only", technical=True)
    active = assign(api, active, REL_MANAGER, "agent", "ag-support-supervisor")
    active = command(api, active, REL_AGENT, "start")
    active = command(api, active, REL_AGENT, "submit",
                     evidence=[{"kind": "work", "ref": "PRIVATE-EVIDENCE", "label": "QA result"}])
    active = command(api, active, REL_ARCHITECT, "request_changes")
    active = command(api, active, REL_AGENT, "start")
    assert snapshot(api, REL_AGENT)["tasks"][0]["evidence"][0]["ref"] == "PRIVATE-EVIDENCE"
    command(api, active, REL_MANAGER, "block", 403)

    config = api.app.state.service.config
    revised = deepcopy(config.get())
    revoked = next(row for row in revised["catalog"]["agents"] if row["id"] == "ag-support-supervisor")
    revoked["eval_status"] = "pending"
    monkeypatch.setattr(config, "get", lambda: revised)
    view = snapshot(api, who("support-agent-subject", "relation-agent", "global-admin"))
    assert view["tasks"] == view["projects"] == view["goals"] == []
    assert view["capabilities"]["agent_id"] is None and not view["capabilities"]["global_admin"]
    assert "PRIVATE-EVIDENCE" not in str(view)
    command(api, active, REL_AGENT, "block", 404)
    command(api, active, REL_AGENT, "report_usage", 404, tokens={"input": 0, "output": 0})
    command(api, active, OPS_MANAGER, "block", 404)
    assert snapshot(api, REL_MANAGER)["tasks"][0]["id"] == active["id"]
    active = command(api, active, REL_MANAGER, "block")
    assert active["status"] == "blocked"
    active = assign(api, active, REL_MANAGER, "agent", "ag-support-research")
    assert command(api, active, REL_RESEARCH, "start")["status"] == "in_progress"


def test_delegation_lineage_auto_selection_and_reported_tokens(tmp_path):
    api = make_client(tmp_path, portfolio_autonomy={REL: {"read_only": "autonomous"}})
    project_id = project(api, 10, REL)
    parent = task(api, 20, project_id, REL, execution="read_only")
    parent = assign(api, parent, REL_MANAGER, "agent", "ag-support-supervisor")
    delegate(api, parent, REL_MANAGER, 21, 409)
    parent = command(api, parent, REL_AGENT, "start")
    delegate(api, parent, OPS_MANAGER, 21, 404)
    delegate(api, parent, REL_RESEARCH, 21, 404)
    delegate(api, parent, REL_AGENT, 21, 403, mode="manual", agent_id="ag-support-research")
    for unavailable in ("ag-doc-research", "ag-support-supervisor", "ag-support-writer"):
        delegate(api, parent, REL_MANAGER, 21, 422, mode="manual", agent_id=unavailable)
    result = delegate(api, parent, REL_MANAGER, 21, mode="manual", agent_id="ag-support-research")
    first = result["task"]
    assert first["parent_task_id"] == parent["id"] and first["execution"] == "read_only"
    assert first["delegation"] == "manual" and first["autonomy"] == "autonomous"
    assert delegate(api, parent, REL_AGENT, 22, 409)["detail"] == "revision_conflict"
    parent = result["parent"]
    assert delegate(api, parent, REL_AGENT, 21, 422, id=first["id"]) == (
        delegate(api, parent, REL_AGENT, 21, 422, id=identifier(99)))
    result = delegate(api, parent, REL_AGENT, 22)
    assert result["task"]["assignee"]["id"] == "ag-support-research"
    assert result["task"]["delegation"] == "automatic"
    assert {row["id"] for row in snapshot(api, REL_AGENT)["tasks"]} == {parent["id"], first["id"], result["task"]["id"]}
    assert {row["id"] for row in snapshot(api, REL_RESEARCH)["tasks"]} == {first["id"], result["task"]["id"]}
    command(api, first, REL_AGENT, "start", 404)
    assert command(api, result["parent"], REL_AGENT, "submit", 409)["detail"] == "delegated_tasks_incomplete"
    first = command(api, first, REL_RESEARCH, "start")
    delegate(api, first, REL_RESEARCH, 23, 409)
    command(api, first, REL_RESEARCH, "submit", 422, tokens={"input": 1, "output": -1})
    command(api, first, REL_RESEARCH, "submit", 422, tokens={"input": 1, "output": 1, "total": 9000})
    command(api, first, REL_RESEARCH, "report_usage", 422)
    command(api, first, REL_AGENT, "report_usage", 404, tokens={"input": 34, "output": 8})
    first = command(api, first, REL_RESEARCH, "report_usage", tokens={"input": 34, "output": 8})
    assert first["tokens"] == {"input": 34, "output": 8, "total": 42, "source": "agent_reported"}
    assert first["status"] == "in_progress"
    assert command(api, first, REL_RESEARCH, "report_usage", tokens={"input": 34, "output": 8}) == first
    assert command(api, first, REL_RESEARCH, "report_usage", 409, tokens={"input": 33, "output": 9})["detail"] == "token_usage_regressed"
    assert next(row for row in snapshot(api, REL_AGENT)["tasks"] if row["id"] == first["id"])["tokens"]["total"] == 42
    assert snapshot(api, OPS_MANAGER)["tasks"] == []
    command(api, first, REL_RESEARCH, "submit", 409, tokens={"input": 35, "output": 7})
    first = command(api, first, REL_RESEARCH, "submit")
    assert first["tokens"]["total"] == 42
    assert first["status"] == "done"
    parent = result["parent"]
    children = [result["task"]]
    for index in (23, 24):
        result = delegate(api, parent, REL_AGENT, index)
        parent = result["parent"]
        children.append(result["task"])
    assert delegate(api, parent, REL_AGENT, 25, 409)["detail"] == "delegation_capacity"
    command(api, parent, REL_MANAGER, "cancel", 409)
    for child in children:
        command(api, child, REL_RESEARCH, "cancel", 403)
        assert command(api, child, REL_MANAGER, "cancel")["status"] == "cancelled"
    parent = command(api, parent, REL_AGENT, "submit")
    assert parent["status"] == "done" and parent["tokens"] is None


def test_automatic_delegation_review_and_load_balance(tmp_path):
    api = make_client(tmp_path, portfolio_autonomy={})
    triage = who("triage-agent-subject", "relation-agent")
    parent = task(api, 20, project(api, 10, REL), REL, execution="prepare", technical=True)
    parent = assign(api, parent, REL_MANAGER, "agent", "ag-triage")
    parent = command(api, parent, REL_MANAGER, "authorize_start")
    parent = command(api, parent, triage, "start")
    assert delegate(api, parent, triage, 21, 403)["detail"] == "portfolio_start_requires_review"
    result = delegate(api, parent, REL_MANAGER, 21)
    first = result["task"]
    assert first["assignee"]["id"] == "ag-marketing-writer"
    assert first["technical"] and first["autonomy"] == "human_review" and not first["start_authorized"]
    command(api, first, who("marketing-agent-subject", "relation-agent"), "start", 403)
    second = delegate(api, result["parent"], REL_MANAGER, 22)["task"]
    assert second["assignee"]["id"] == "ag-support-research"
