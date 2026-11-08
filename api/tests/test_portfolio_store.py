"""Portfolio state invariants, independent of HTTP capabilities."""

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier

import pytest

from app.portfolio_store import PortfolioActor, PortfolioError, PortfolioStore


PLANNER = PortfolioActor("subject:planner", "Chef de projet")
MANAGER = PortfolioActor("subject:manager", "Manager")
ARCHITECT = PortfolioActor("subject:architect", "Architecte")
EMPLOYEE = PortfolioActor("employee:p01", "Employé")
AGENT = PortfolioActor("agent:ag-1", "Agent")
CHILD_AGENT = PortfolioActor("agent:ag-2", "Agent délégué")
NOW = dt.datetime(2026, 11, 7, 12, tzinfo=dt.UTC)


def key(n):
    return f"{n:032x}"


def allowed(task):
    assert task["unit"] in {"ops", "sales"}


def tasks(store, units=("ops",), own=()):
    return store.snapshot(managed_units=frozenset(units), own_assignments=own)["tasks"]


def create_task(store, number, **fields):
    data = {"title": f"Task {number}", "project_id": key(2), "unit": "ops", "execution": "human", **fields}
    return store.create_task(key(number), data, PLANNER)


def command(store, task, action, actor=EMPLOYEE, *, authorize=allowed, **fields):
    return store.command_task(task["id"], task["revision"], {"action": action, **fields}, actor, authorize,
                              workbench_aliases=(actor.ref,))


def assign(store, task, kind="employee", identity="p01"):
    return command(store, task, "assign", MANAGER, assignee={"kind": kind, "id": identity})


def delegate(store, parent, number, choose=lambda task, load: "ag-2", mode="automatic"):
    return store.delegate_task(parent["id"], parent["revision"], key(number),
                               {"title": f"Subtask {number}", "delegation": mode}, AGENT, choose,
                               workbench_available=True)


@pytest.fixture
def store(tmp_path):
    service = PortfolioStore(tmp_path, clock=lambda: NOW)
    service.create_goal(key(1), {"title": "Reduce delays", "metric": "Delay in hours", "target": 0,
                                 "direction": "at_most"}, PLANNER)
    service.create_project(key(2), {"title": "Shared project", "goal_id": key(1), "owner_unit": "ops",
                                    "unit_ids": ["ops", "sales"], "state": "active"}, PLANNER)
    return service


def test_snapshot_does_not_create_storage(tmp_path):
    directory = tmp_path / "uncreated"
    service = PortfolioStore(directory)
    assert service.snapshot(managed_units=frozenset({"ops"}), own_assignments=()) == {
        "goals": [], "projects": [], "tasks": [], "truncated": False,
    }
    assert not directory.exists()


def test_global_hierarchy_and_metric_revisions(store):
    snapshot = store.snapshot(managed_units=frozenset(), own_assignments=())
    goal, = snapshot["goals"]
    project, = snapshot["projects"]
    assert goal["current"] is None and goal["direction"] == "at_most" and goal["target"] == 0
    assert project["goal_id"] == goal["id"]
    assert project["unit_ids"] == ["ops", "sales"]
    assert store.update_goal(goal["id"], 1, {"current": 0}, PLANNER)["current"] == 0
    with pytest.raises(PortfolioError, match="revision_conflict"):
        store.update_goal(goal["id"], 1, {"current": 1}, PLANNER)
    with pytest.raises(PortfolioError, match="goal_not_found"):
        store.create_project(key(3), {"title": "Missing goal", "goal_id": key(4), "owner_unit": "ops",
                                      "unit_ids": ["ops"]}, PLANNER)
    with pytest.raises(PortfolioError, match="project_not_found"):
        create_task(store, 5, unit="foreign")
    create_task(store, 6)
    with pytest.raises(PortfolioError, match="project_has_open_tasks"):
        store.update_project(project["id"], 1, {"state": "done"}, PLANNER)
    updated = store.update_project(project["id"], 1, {"state": "archived", "unit_ids": ["foreign"]}, PLANNER)
    assert updated["unit_ids"] == ["ops", "sales"]
    with pytest.raises(PortfolioError, match="project_closed"):
        create_task(store, 7)


def test_scope_union_includes_only_managed_and_exact_assignments(store):
    ops = assign(store, create_task(store, 3))
    sales = assign(store, create_task(store, 4, unit="sales"), identity="p02")
    agent = assign(store, create_task(store, 5, execution="read_only", unit="sales"), "agent", "p01")
    own_sales = assign(store, create_task(store, 6, unit="sales"))

    assert {row["id"] for row in tasks(store, (), (("ops", "employee", "p01"),))} == {ops["id"]}
    assert {row["id"] for row in tasks(store, (), (("sales", "agent", "p01"),))} == {agent["id"]}
    scoped = tasks(store, ("ops",), (("sales", "employee", "p01"),))
    assert {row["id"] for row in scoped} == {ops["id"], own_sales["id"]}
    assert sales["id"] not in {row["id"] for row in scoped}
    assert all("actor_ref" not in event for row in scoped for event in row["history"])


def test_dependency_gate_cycle_and_scope_failures_are_atomic(store):
    first = create_task(store, 3)
    second = create_task(store, 4, dependency_ids=[first["id"]])
    cross_unit = create_task(store, 5, unit="sales")
    store.create_project(key(9), {"title": "Other project", "goal_id": key(1), "owner_unit": "ops",
                                  "unit_ids": ["ops"]}, PLANNER)
    cross_project = create_task(store, 10, project_id=key(9))
    for bad_dep in (cross_unit["id"], cross_project["id"], key(100)):
        with pytest.raises(PortfolioError, match="dependency_unavailable"):
            create_task(store, 6, dependency_ids=[bad_dep])
    with pytest.raises(PortfolioError, match="dependency_cycle"):
        create_task(store, 6, dependency_ids=[key(6)])
    with pytest.raises(PortfolioError, match="dependency_cycle"):
        store.update_task(first["id"], 1, {"dependency_ids": [second["id"]]}, PLANNER, allowed)
    assert key(6) not in {row["id"] for row in tasks(store)}
    assert next(row for row in tasks(store) if row["id"] == first["id"])["revision"] == 1

    second = assign(store, second)
    assert second["dependencies_ready"] is False
    with pytest.raises(PortfolioError, match="dependencies_incomplete"):
        command(store, second, "start")
    first = command(store, assign(store, first), "start")
    first = command(store, first, "submit", evidence=[{"kind": "work", "label": "Report", "ref": "DOC-2"}])
    assert first["status"] == "done"
    assert next(row for row in tasks(store) if row["id"] == second["id"])["dependencies_ready"] is True
    assert command(store, second, "start")["status"] == "in_progress"


def test_authority_runs_before_revision_and_mutation(store):
    task = assign(store, create_task(store, 3))

    def deny(current):
        assert current["assignee"] == {"kind": "employee", "id": "p01"}
        raise PortfolioError("task_not_found", 404)

    with pytest.raises(PortfolioError, match="task_not_found") as denied:
        store.command_task(task["id"], 1, {"action": "start"}, EMPLOYEE, deny)
    assert denied.value.status == 404
    stored, = tasks(store)
    assert stored == task
    with pytest.raises(PortfolioError, match="duplicate_id"):
        create_task(store, 3)


def test_agent_grant_binds_assignment_and_revision_and_is_consumed(store):
    planned = create_task(store, 3, execution="prepare")
    with pytest.raises(PortfolioError, match="assignee_incompatible"):
        assign(store, planned)
    ready = assign(store, planned, "agent", "ag-1")

    def reviewed_bu(current):
        if not current["start_authorized"]:
            raise PortfolioError("start_authorization_required", 403)

    with pytest.raises(PortfolioError, match="start_authorization_required"):
        command(store, ready, "start", AGENT, authorize=reviewed_bu)
    granted = command(store, ready, "authorize_start", MANAGER)
    assert granted["start_authorized"] is True
    replanned = store.update_task(granted["id"], granted["revision"], {"priority": "high"}, PLANNER, allowed)
    assert replanned["start_authorized"] is False
    granted = command(store, replanned, "authorize_start", MANAGER)
    reassigned = assign(store, granted, "agent", "ag-2")
    assert reassigned["start_authorized"] is False
    with pytest.raises(PortfolioError, match="revision_conflict"):
        command(store, granted, "start", AGENT)
    granted = command(store, reassigned, "authorize_start", MANAGER)
    working = command(store, granted, "start", AGENT, authorize=reviewed_bu)
    assert working["status"] == "in_progress" and working["start_authorized"] is False
    blocked = command(store, working, "block", AGENT)
    ready_again = command(store, blocked, "unblock", AGENT)
    assert ready_again["status"] == "ready" and ready_again["start_authorized"] is False


def test_agent_grant_must_match_current_policy_without_disclosing_fingerprint(store):
    ready = assign(store, create_task(store, 3, execution="read_only"), "agent", "ag-1")

    def run(task, action, policy_fingerprint, actor=MANAGER, authorize=allowed):
        return store.command_task(task["id"], task["revision"], {"action": action}, actor, authorize,
                                  policy_fingerprint=policy_fingerprint)

    def requires_grant(current):
        if not current["start_authorized"]:
            raise PortfolioError("start_authorization_required", 403)

    def snapshot(policy_fingerprint):
        task, = store.snapshot(managed_units=frozenset({"ops"}), own_assignments=(),
                               policy_fingerprint=policy_fingerprint)["tasks"]
        return task

    granted = run(ready, "authorize_start", "old-fingerprint")
    assert granted["start_authorized"] is True
    assert snapshot("old-fingerprint") == granted
    assert snapshot("new-fingerprint")["start_authorized"] is False
    assert snapshot(None)["start_authorized"] is False
    assert "old-fingerprint" not in repr(granted)
    with pytest.raises(PortfolioError, match="start_authorization_required"):
        run(granted, "start", "new-fingerprint", AGENT, requires_grant)
    regranted = run(granted, "authorize_start", "new-fingerprint")
    assert snapshot("old-fingerprint")["start_authorized"] is False
    assert run(regranted, "start", "new-fingerprint", AGENT, requires_grant)["status"] == "in_progress"


@pytest.mark.parametrize("execution,technical,submitted,approved", [
    ("human", False, "done", None),
    ("human", True, "review", "done"),
    ("read_only", False, "done", None),
    ("prepare", False, "done", None),
    ("prepare", True, "review", "done"),
    ("sensitive", False, "workbench", None),
    ("sensitive", True, "review", "workbench"),
])
def test_review_and_workbench_paths(store, execution, technical, submitted, approved):
    task = create_task(store, 3, execution=execution, technical=technical)
    kind, identity, actor = ("employee", "p01", EMPLOYEE) if execution == "human" else ("agent", "ag-1", AGENT)
    task = command(store, assign(store, task, kind, identity), "start", actor)
    task = command(store, task, "submit", actor)
    assert task["status"] == submitted
    if approved:
        with pytest.raises(PortfolioError, match="separation_of_duties"):
            command(store, task, "approve", PortfolioActor(actor.ref, "Other role"))
        task = command(store, task, "approve", ARCHITECT)
        assert task["status"] == approved
    with pytest.raises(PortfolioError, match="invalid_transition"):
        command(store, task, "approve", ARCHITECT)


def test_rework_releases_review_with_no_reused_start_grant(store):
    task = assign(store, create_task(store, 3, execution="prepare", technical=True), "agent", "ag-1")
    task = command(store, command(store, task, "authorize_start", MANAGER), "start", AGENT)
    task = command(store, task, "submit", AGENT)
    task = command(store, task, "request_changes", ARCHITECT)
    assert task["status"] == "ready" and task["start_authorized"] is False


def test_concurrent_start_commits_once_with_one_history_entry(store):
    ready = assign(store, create_task(store, 3))
    barrier = Barrier(2)

    def try_start():
        barrier.wait(timeout=3)
        try:
            return command(store, ready, "start")["status"]
        except PortfolioError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: try_start(), range(2)))
    assert sorted(results) == ["in_progress", "revision_conflict"]
    task, = tasks(store)
    assert [row["action"] for row in task["history"]] == ["start", "assign", "create"]


def test_task_and_history_limits_apply_after_scope_filtering(store, monkeypatch):
    monkeypatch.setattr("app.portfolio_store._TASK_LIMIT", 2)
    monkeypatch.setattr("app.portfolio_store._HISTORY_LIMIT", 2)
    for index in range(3, 6):
        assign(store, create_task(store, index, unit="sales"))
    own = assign(store, create_task(store, 6))
    current = store.update_task(own["id"], own["revision"], {"priority": "critical"}, PLANNER, allowed)
    view = store.snapshot(managed_units=frozenset(), own_assignments=(("ops", "employee", "p01"),))
    assert view["truncated"] is False and view["tasks"] == [current]
    assert [row["action"] for row in current["history"]] == ["update", "assign"]
    full = store.snapshot(managed_units=frozenset({"ops", "sales"}), own_assignments=())
    assert full["truncated"] is True and len(full["tasks"]) == 2


def test_snapshot_batches_history_reads(store, monkeypatch):
    rows = [create_task(store, number) for number in range(3, 9)]
    assigned = assign(store, rows[-1])
    transaction = store._transaction
    history_reads = []

    @contextmanager
    def trace_transaction(*, write):
        with transaction(write=write) as db:
            db.set_trace_callback(lambda sql: history_reads.append(sql) if "FROM task_history" in sql or
                                  "JOIN task_history" in sql else None)
            yield db

    monkeypatch.setattr(store, "_transaction", trace_transaction)
    snapshot = store.snapshot(managed_units=frozenset({"ops"}), own_assignments=(), limit=4)
    assert len(history_reads) == 1
    assert snapshot["tasks"][0]["history"] == assigned["history"]
    assert all(len(row["history"]) == 1 for row in snapshot["tasks"][1:])


@pytest.mark.parametrize("execution,child_execution", [("read_only", "read_only"), ("sensitive", "prepare")])
def test_delegation_creates_scoped_lineage_and_requires_child_resolution(store, execution, child_execution):
    parent = assign(store, create_task(store, 3, execution=execution, technical=True), "agent", "ag-1")
    with pytest.raises(PortfolioError, match="invalid_transition"):
        delegate(store, parent, 4)
    parent = command(store, parent, "start", AGENT)
    with pytest.raises(PortfolioError, match="assignee_incompatible"):
        delegate(store, parent, 4, lambda task, load: "ag-1")

    first = delegate(store, parent, 4, mode="manual")
    current, child = first["parent"], first["task"]
    assert current["revision"] == parent["revision"] + 1 and current["status"] == "in_progress"
    assert current["history"][0]["action"] == "delegate_task"
    assert (child["parent_task_id"], child["project_id"], child["unit"]) == (parent["id"], key(2), "ops")
    assert child["status"] == "ready" and child["execution"] == child_execution and child["technical"] is True
    assert child["delegation"] == "manual" and child["tokens"] is None and child["start_authorized"] is False
    assert {row["id"] for row in tasks(store, (), (("ops", "agent", "ag-1"),))} == {parent["id"], child["id"]}
    assert {row["id"] for row in tasks(store, (), (("ops", "agent", "ag-2"),))} == {child["id"]}
    assert not tasks(store, ("sales",), (("sales", "agent", "ag-1"),))
    with pytest.raises(PortfolioError, match="revision_conflict"):
        delegate(store, parent, 4)
    with pytest.raises(PortfolioError, match="duplicate_id"):
        delegate(store, current, 4)
    with pytest.raises(PortfolioError, match="dependency_cycle"):
        store.update_task(child["id"], child["revision"], {"dependency_ids": [parent["id"]]}, PLANNER, allowed)
    with pytest.raises(PortfolioError, match="delegated_tasks_incomplete"):
        command(store, current, "submit", AGENT)

    child = command(store, child, "start", CHILD_AGENT)
    with pytest.raises(PortfolioError, match="invalid_transition"):
        delegate(store, child, 5)
    child_usage = {"input": 10, "output": 4, "total": 14}
    child = command(store, child, "submit", CHILD_AGENT, tokens=child_usage)
    assert child["tokens"] == child_usage
    assert child["status"] == "review"
    with pytest.raises(PortfolioError, match="delegated_tasks_incomplete"):
        command(store, current, "submit", AGENT)
    child = command(store, child, "approve", ARCHITECT)
    assert child["tokens"] == child_usage and child["status"] == "done"
    parent_usage = {"input": 20, "output": 8, "total": 28}
    current = command(store, current, "submit", AGENT, tokens=parent_usage)
    assert current["status"] == "review" and current["tokens"] == parent_usage
    assert {row["id"]: row["tokens"] for row in tasks(store)} == {parent["id"]: parent_usage,
                                                                  child["id"]: child_usage}


def test_delegation_cap_includes_cancelled_and_cancel_has_no_parent_side_effect(store):
    parent = assign(store, create_task(store, 3, execution="prepare"), "agent", "ag-1")
    parent = command(store, parent, "start", AGENT)
    observed_load = []

    def choose(task, load):
        observed_load.append(dict(load))
        return min(("ag-2", "ag-3"), key=lambda candidate: (load.get(candidate, 0), candidate))

    for number in range(4, 8):
        result = delegate(store, parent, number, choose)
        parent = result["parent"]
        cancelled = command(store, result["task"], "cancel", MANAGER)
        assert cancelled["status"] == "cancelled" and cancelled["history"][0]["action"] == "cancel"
        with pytest.raises(PortfolioError, match="invalid_transition"):
            command(store, cancelled, "start", CHILD_AGENT)
    assert observed_load == [{}, {"ag-2": 1}, {"ag-2": 1, "ag-3": 1}, {"ag-2": 2, "ag-3": 1}]
    with pytest.raises(PortfolioError, match="delegation_capacity"):
        delegate(store, parent, 8, choose)
    with pytest.raises(PortfolioError, match="invalid_transition"):
        command(store, parent, "cancel", MANAGER)
    assert command(store, parent, "submit", AGENT)["status"] == "done"
    assert store.update_project(key(2), 1, {"state": "done"}, PLANNER)["state"] == "done"


def test_concurrent_delegation_claims_one_parent_revision(store):
    parent = assign(store, create_task(store, 3, execution="prepare"), "agent", "ag-1")
    parent = command(store, parent, "start", AGENT)
    barrier = Barrier(2)

    def try_delegate(child_id):
        barrier.wait(timeout=3)
        try:
            return delegate(store, parent, child_id)["task"]["status"]
        except PortfolioError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(try_delegate, (4, 5))) == ["ready", "revision_conflict"]
    stored = tasks(store)
    assert len(stored) == 2
    root, = (row for row in stored if row["parent_task_id"] is None)
    assert sum(event["action"] == "delegate_task" for event in root["history"]) == 1


def test_reassignment_cannot_merge_parent_and_child_agent_bindings(store):
    parent = assign(store, create_task(store, 3, execution="prepare"), "agent", "ag-1")
    result = delegate(store, command(store, parent, "start", AGENT), 4)
    child = result["task"]
    with pytest.raises(PortfolioError, match="assignee_incompatible"):
        assign(store, child, "agent", "ag-1")
    parent = command(store, result["parent"], "block", AGENT)
    with pytest.raises(PortfolioError, match="assignee_incompatible"):
        assign(store, parent, "agent", "ag-2")
    parent = assign(store, parent, "agent", "ag-3")
    assert not tasks(store, (), (("ops", "agent", "ag-1"),))
    assert {row["id"] for row in tasks(store, (), (("ops", "agent", "ag-3"),))} == {parent["id"], child["id"]}


def test_live_token_usage_is_monotone_idempotent_and_survives_submit(store):
    parent = assign(store, create_task(store, 3, execution="read_only"), "agent", "ag-1")
    report = {"input": 12, "output": 5, "total": 17}
    with pytest.raises(PortfolioError, match="invalid_transition"):
        command(store, parent, "report_usage", AGENT, tokens=report)
    parent = command(store, parent, "start", AGENT)
    assert parent["tokens"] is None
    parent = command(store, parent, "report_usage", AGENT, tokens=report)
    assert parent["tokens"] == report and parent["history"][0]["action"] == "report_usage"
    assert command(store, parent, "report_usage", AGENT, tokens=report) == parent
    with pytest.raises(PortfolioError, match="token_usage_regressed"):
        command(store, parent, "report_usage", AGENT, tokens={"input": 10, "output": 8, "total": 18})
    with pytest.raises(PortfolioError, match="token_usage_regressed"):
        command(store, parent, "submit", AGENT, tokens={"input": 13, "output": 4, "total": 17})
    parent = command(store, parent, "block", AGENT)
    same_agent = assign(store, parent, "agent", "ag-1")
    assert same_agent["tokens"] == report
    parent = command(store, same_agent, "start", AGENT)
    parent = command(store, parent, "submit", AGENT)
    assert parent["tokens"] == report and parent["status"] == "done"

    working = command(store, assign(store, create_task(store, 4, execution="prepare"), "agent", "ag-1"),
                      "start", AGENT)
    working = command(store, working, "report_usage", AGENT, tokens=report)
    reassigned = assign(store, command(store, working, "block", AGENT), "agent", "ag-2")
    assert reassigned["tokens"] is None
    employee = command(store, assign(store, create_task(store, 5)), "start")
    with pytest.raises(PortfolioError, match="portfolio_agent_tokens_only"):
        command(store, employee, "report_usage", EMPLOYEE, tokens=report)
