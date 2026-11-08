# SPDX-License-Identifier: MIT
"""Exercise synthetic Portfolio and Workbench state through the Kind fixture's trusted loopback."""

import json
import sys
import time
import urllib.request
from uuid import uuid4

API = "http://127.0.0.1:8000/api/v1"
PORTFOLIO = "/portfolio"
CASES = "/workbench/cases"


def call(path, subject, group, payload=None):
    headers = {"X-Forwarded-User": subject, "X-Forwarded-Email": f"{subject}@factory.test",
               "X-Forwarded-Groups": group}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        API + path, headers=headers, data=json.dumps(payload).encode() if payload is not None else None)
    with urllib.request.urlopen(request, timeout=5) as result:
        return json.load(result)


def wait_for(check):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if result := check():
            return result
        time.sleep(1)
    raise AssertionError("Portfolio reconciliation timed out")


def submit():
    goal_id, project_id = uuid4().hex, uuid4().hex
    call(PORTFOLIO + "/goals", "restore-director", "admins", {
        "id": goal_id, "title": "Recovery rehearsal", "metric": "Synthetic evidence count", "target": 1,
    })
    call(PORTFOLIO + "/projects", "restore-director", "admins", {
        "id": project_id, "goal_id": goal_id, "title": "Recovery probe", "state": "active",
        "owner_unit": "cc-ops", "unit_ids": ["cc-ops"], "usecase_id": "uc-01",
    })

    def ops(path, payload):
        return call(PORTFOLIO + path, "restore-operator", "ops", payload)

    task = ops("/tasks", {"project_id": project_id, "unit": "cc-ops", "title": "Recover synthetic decision",
                          "description": "Review a fixture with no external effects.", "execution": "sensitive"})
    commands = f"/tasks/{task['id']}/commands"
    task = ops(commands, {"action": "assign", "revision": task["revision"],
                          "assignee": {"kind": "agent", "id": "ag-doc-research"}})
    task = ops(commands, {"action": "authorize_start", "revision": task["revision"]})

    def agent(action, **fields):
        return call(PORTFOLIO + commands, "restore-agent", "ops-agents", {
            "action": action, "revision": task["revision"], **fields,
        })

    task = agent("start")
    task = agent("submit", evidence=[{"kind": "work", "label": "Synthetic probe", "ref": "RESTORE-01"}])
    assert task["status"] == "workbench"
    [case] = wait_for(lambda: call(CASES, "restore-reviewer", "admins")["cases"])
    assert case["status"] == "waiting_review" and case["can_decide"]
    print(task["id"], case["id"], case["revision"], case["proposal_hash"])


def approve(task_id, case_id, revision, proposal_hash):
    [case] = call(CASES, "restore-reviewer", "admins")["cases"]
    assert (case["id"], case["revision"], case["proposal_hash"]) == (case_id, int(revision), proposal_hash)
    result = call(f"{CASES}/{case_id}/decisions", "restore-reviewer", "admins", {
        "decision": "approve", "revision": int(revision), "proposal_hash": proposal_hash,
        "rationale": "Synthetic recovery verification. No external effect.",
    })
    assert result["status"] == "decided"

    def reconciled():
        tasks = call(PORTFOLIO, "restore-operator", "ops")["tasks"]
        return next((row for row in tasks if row["id"] == task_id and row["status"] == "done"), None)

    row = wait_for(reconciled)
    assert row["workbench_decision"]["outcome"] == "approve"
    assert row["history"][0]["action"] == "workbench_approve"
    assert not call(PORTFOLIO, "restore-unrelated", "finance")["tasks"]
    print("PASS restored Portfolio reflects the independent Workbench approval")


if __name__ == "__main__":
    match sys.argv[1:]:
        case ["submit"]:
            submit()
        case ["approve", task_id, case_id, revision, proposal_hash]:
            approve(task_id, case_id, revision, proposal_hash)
        case _:
            sys.exit("Usage: restore_workbench.py submit|approve TASK_ID CASE_ID REVISION PROPOSAL_HASH")
