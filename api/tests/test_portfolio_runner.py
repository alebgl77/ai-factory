"""Real HTTP task lifecycle with a private, injected inference gateway."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import shutil
import threading
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import create_app
from app.portfolio_runner import LEASE_SECONDS, MAX_TOKENS, _load_bindings
from app.settings import Settings
from tests.conftest import NOW, TODAY
from tests.test_bu_access import CONFIG, ROLE_MAP, policy_file
from tests.test_portfolio import (
    BINDINGS, GLOBAL, GROUPS, OPS_MANAGER, PORTFOLIO, PORTFOLIO_ROLES, REL, REL_AGENT, REL_ARCHITECT,
    REL_MANAGER, REL_RESEARCH, assign, command, delegate, project, snapshot, task,
)
from tests.test_security import TRUSTED_PEER

KEY = "sk-fixture-runner-1234567890"
MASTER = "sk-fixture-master-1234567890"
WORK = "Réponse de test privée."


@pytest.fixture
def setup(tmp_path):
    config = tmp_path / "config"
    shutil.copytree(CONFIG, config)
    case_path = config / "usecases" / "uc-02.yaml"
    case = yaml.safe_load(case_path.read_text(encoding="utf-8"))
    case["sensitivity"] = "C3"
    case["model"]["alias"] = "chat-sensitive"
    case_path.write_text(yaml.safe_dump(case, allow_unicode=True), encoding="utf-8")
    secret = tmp_path / "runner.json"
    secret.write_text(json.dumps({"version": 1, "bindings": [
        {"agent_id": "ag-support-research", "unit": REL, "usecase_id": "uc-02", "virtual_key": KEY},
    ]}), encoding="utf-8")
    roles = ROLE_MAP + ("finance-admin:admin",) + tuple(
        f"{group}:viewer" for group in GROUPS if group.startswith(("ops-", "relation-"))
    )
    clock = [NOW]

    def config_for(mode="autonomous"):
        policy = policy_file(tmp_path, groups=GROUPS, portfolio_roles=PORTFOLIO_ROLES,
                             portfolio_agents=BINDINGS, portfolio_autonomy={
                                 REL: {"read_only": "autonomous", "prepare": mode},
                             })
        return Settings(config_dir=str(config), state_dir=str(tmp_path / "state"), demo_mode="never",
                        demo_date=TODAY.isoformat(), auth_mode="proxy", trusted_proxies=("10.0.0.0/24",),
                        role_map=roles, bu_policy_path=policy, portfolio_runner_file=str(secret),
                        litellm_url="http://litellm:4000", litellm_master_key=MASTER)

    def build(handler, *, mode="autonomous", settings=None):
        app = create_app(settings or config_for(mode), clock=lambda: clock[0],
                         portfolio_runner_transport=httpx.MockTransport(handler))
        return TestClient(app, client=TRUSTED_PEER)

    return build, config_for, clock


def completion(content=WORK, finish="stop"):
    return httpx.Response(200, json={"choices": [{"finish_reason": finish, "message": {"content": content}}],
                                     "usage": {"prompt_tokens": 17, "completion_tokens": 9}})


def queue(api, *, technical=False, automatic=False, execution="read_only"):
    project_id = project(api, unit=REL)
    parent = task(api, 1, project_id, REL, execution=execution, technical=technical)
    parent = assign(api, parent, REL_MANAGER, "agent", "ag-support-supervisor")
    if parent["autonomy"] == "human_review":
        parent = command(api, parent, REL_MANAGER, "authorize_start")
    parent = command(api, parent, REL_AGENT, "start")
    delegated = delegate(api, parent, REL_MANAGER, 2, **(
        {} if automatic else {"mode": "manual", "agent_id": "ag-support-research"}
    ))
    return delegated["parent"], delegated["task"]


def row(api, task_id, actor=REL_MANAGER):
    return next(item for item in snapshot(api, actor)["tasks"] if item["id"] == task_id)


def test_lifespan_executes_and_projects_a_bound_child_only(setup, monkeypatch):
    seen = []
    event = threading.Event()
    from app import main

    def handler(request):
        seen.append(request)
        event.set()
        return completion()

    monkeypatch.setattr(main, "POLL_SECONDS", 0.01)
    build, _, _ = setup
    api = build(handler)
    parent, child = queue(api, automatic=True)
    assert child["assignee"]["id"] == "ag-support-research"
    assert child["runner_managed"] and not parent["runner_managed"]
    assert command(api, child, REL_RESEARCH, "start", 403)["detail"] == "portfolio_runner_managed"
    with api:
        assert event.wait(timeout=5)
        for _ in range(30):
            actual = row(api, child["id"])
            if actual["status"] == "done":
                break
        assert actual["status"] == "done" and actual["runner_result"] == WORK
        assert actual["tokens"] == {"input": 17, "output": 9, "total": 26, "source": "provider_reported"}
        assert actual["runner_finished_at"] >= actual["runner_started_at"]
        assert actual["evidence"][0]["ref"].startswith("run-")
        assert [entry["action"] for entry in actual["history"]] == ["submit", "runner_start", "create"]
        assert row(api, parent["id"])["status"] == "in_progress"
        assert not snapshot(api, OPS_MANAGER)["tasks"]
    request, = seen
    assert str(request.url) == "http://litellm:4000/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert set(body) == {"model", "stream", "max_tokens", "metadata", "messages"}
    assert body["model"] == "chat-sensitive" and body["metadata"] == {"usecase": "uc-02"}
    assert body["max_tokens"] == MAX_TOKENS
    assert json.loads(body["messages"][1]["content"]) == {"title": child["title"], "description": child["description"]}


def test_single_claim_grant_and_technical_review(setup):
    build, _, _ = setup
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def handler(request):
        calls.append(request)
        entered.set()
        await release.wait()
        return completion()

    api = build(handler, mode="human_review")
    _, child = queue(api, technical=True, execution="prepare")
    engine = api.app.state.portfolio_engine
    assert not asyncio.run(engine.run_once())
    command(api, child, REL_MANAGER, "authorize_start")

    async def race():
        attempt = asyncio.create_task(engine.run_once())
        await asyncio.wait_for(entered.wait(), timeout=4)
        try:
            assert not await engine.run_once()
            running = await asyncio.to_thread(row, api, child["id"])
            assert running["status"] == "in_progress" and not running["start_authorized"]
            denial = await asyncio.to_thread(command, api, running, REL_RESEARCH, "submit", 403)
            assert denial["detail"] == "portfolio_runner_managed"
        finally:
            release.set()
        assert await attempt

    asyncio.run(race())
    reviewed = row(api, child["id"])
    assert reviewed["status"] == "review" and len(calls) == 1
    command(api, reviewed, REL_ARCHITECT, "approve")
    assert row(api, child["id"])["status"] == "done"


@pytest.mark.parametrize("cancel", [False, True])
def test_in_flight_revocation_and_cancellation_discard_late_output(setup, cancel):
    build, settings, _ = setup
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(_):
        entered.set()
        await release.wait()
        return completion("This stale text must not persist.")

    cfg = settings()
    api = build(handler, settings=cfg)
    _, child = queue(api)
    engine = api.app.state.portfolio_engine

    async def race():
        attempt = asyncio.create_task(engine.run_once())
        await asyncio.wait_for(entered.wait(), timeout=4)
        try:
            if cancel:
                running = await asyncio.to_thread(row, api, child["id"])
                await asyncio.to_thread(command, api, running, REL_MANAGER, "cancel")
            else:
                catalogue = Path(cfg.config_dir) / "catalog" / "agents.yaml"
                contents = yaml.safe_load(catalogue.read_text(encoding="utf-8"))
                for agent in contents["agents"]:
                    if agent["id"] == child["assignee"]["id"]:
                        agent["eval_status"] = "failed"
                catalogue.write_text(yaml.safe_dump(contents, allow_unicode=True), encoding="utf-8")
        finally:
            release.set()
        assert await attempt

    asyncio.run(race())
    current = row(api, child["id"])
    assert current["status"] == ("cancelled" if cancel else "blocked")
    assert current["runner_result"] is None and current["tokens"] is None
    if not cancel:
        assert current["runner_error"] == "authorization_changed"
        assert not snapshot(api, REL_RESEARCH)["tasks"]


def test_expired_claim_recovers_after_restart_without_replay(setup):
    build, settings, clock = setup

    def forbidden(_):
        pytest.fail("An uncertain attempt must never replay")

    cfg = settings()
    api = build(forbidden, settings=cfg)
    _, child = queue(api)
    lost = api.app.state.portfolio_engine._select()
    assert lost.task["id"] == child["id"]
    restarted = build(forbidden, settings=cfg)
    assert not asyncio.run(restarted.app.state.portfolio_engine.run_once())
    clock[0] += dt.timedelta(seconds=LEASE_SECONDS + 1)
    assert not asyncio.run(restarted.app.state.portfolio_engine.run_once())
    current = row(restarted, child["id"])
    assert current["status"] == "blocked" and current["runner_error"] == "interrupted"
    assert current["runner_result"] is None
    assert assign(restarted, current, REL_MANAGER, "agent", "ag-support-research")["status"] == "ready"


@pytest.mark.parametrize("restart_parent", [False, True])
def test_child_is_bound_to_parent_start_epoch_and_not_other_parent_revisions(setup, restart_parent):
    build, _, _ = setup
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(_):
        entered.set()
        await release.wait()
        return completion()

    api = build(handler)
    parent, child = queue(api)
    engine = api.app.state.portfolio_engine

    def change_parent():
        if restart_parent:
            blocked = command(api, parent, REL_AGENT, "block")
            ready = assign(api, blocked, REL_MANAGER, "agent", "ag-support-supervisor")
            command(api, ready, REL_AGENT, "start")
        else:
            delegate(api, parent, REL_MANAGER, 3, mode="manual", agent_id="ag-support-research")

    async def race():
        attempt = asyncio.create_task(engine.run_once())
        await asyncio.wait_for(entered.wait(), timeout=4)
        try:
            await asyncio.to_thread(change_parent)
        finally:
            release.set()
        assert await attempt

    asyncio.run(race())
    current = row(api, child["id"])
    assert current["status"] == ("blocked" if restart_parent else "done")
    assert current["runner_result"] == (None if restart_parent else WORK)
    if restart_parent:
        assert current["runner_error"] == "authorization_changed"


def test_archive_and_reopen_in_flight_blocks_stale_result_and_keeps_manager_recovery(setup):
    build, _, _ = setup
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(_):
        entered.set()
        await release.wait()
        return completion()

    api = build(handler)
    parent, child = queue(api)
    engine = api.app.state.portfolio_engine

    def archive_and_reopen():
        current = next(item for item in snapshot(api, GLOBAL)["projects"] if item["id"] == parent["project_id"])
        url = f"{PORTFOLIO}/projects/{current['id']}"
        archived = api.put(url, headers=GLOBAL, json={"revision": current["revision"], "state": "archived"})
        assert archived.status_code == 200, archived.text
        reopened = api.put(url, headers=GLOBAL, json={"revision": archived.json()["revision"], "state": "active"})
        assert reopened.status_code == 200, reopened.text

    async def race():
        attempt = asyncio.create_task(engine.run_once())
        await asyncio.wait_for(entered.wait(), timeout=4)
        try:
            await asyncio.to_thread(archive_and_reopen)
        finally:
            release.set()
        assert await attempt

    asyncio.run(race())
    current = row(api, child["id"])
    assert current["status"] == "blocked" and current["runner_error"] == "authorization_changed"
    assert current["runner_result"] is None
    ready = assign(api, current, REL_MANAGER, "agent", "ag-support-research")
    assert ready["status"] == "ready"
    assert command(api, ready, REL_RESEARCH, "start", 403)["detail"] == "portfolio_runner_managed"


def test_gateway_route_removal_does_not_allow_external_start_of_managed_child(setup):
    build, settings, _ = setup
    cfg = settings()
    api = build(lambda _: pytest.fail("A removed route must never dispatch"), settings=cfg)
    _, child = queue(api)
    case_path = Path(cfg.config_dir) / "usecases" / "uc-02.yaml"
    case = yaml.safe_load(case_path.read_text(encoding="utf-8"))
    case["sensitivity"] = "C2"
    case["model"]["alias"] = "chat-default"
    case_path.write_text(yaml.safe_dump(case, allow_unicode=True), encoding="utf-8")
    current = row(api, child["id"])
    assert current["runner_managed"] and not current["runner_available"]
    assert command(api, current, REL_RESEARCH, "start", 403)["detail"] == "portfolio_runner_managed"
    assert not asyncio.run(api.app.state.portfolio_engine.run_once())


def test_master_invalid_file_and_external_gateway_fail_at_boot(setup, tmp_path):
    build, settings, _ = setup
    cfg = settings()
    with pytest.raises(ValueError, match="loopback gateway URL"):
        build(completion, settings=replace(cfg, litellm_url="https://outside.example:443"))
    file = Path(cfg.portfolio_runner_file)
    with pytest.raises(ValueError, match="RUNNER_FILE") as error:
        _load_bindings(str(file), KEY)
    assert KEY not in str(error.value)
    bad = tmp_path / "bad.json"
    bad.write_text('{"version":1,"version":1,"bindings":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="RUNNER_FILE"):
        _load_bindings(str(bad), MASTER)
    bad.write_bytes(b"a" * 65537)
    with pytest.raises(ValueError, match="RUNNER_FILE"):
        _load_bindings(str(bad), MASTER)


@pytest.mark.parametrize(("response", "reason"), [
    (lambda: httpx.Response(307, headers={"location": "https://outside.example/"}), "gateway_unavailable"),
    (lambda: completion(""), "incomplete_output"),
    (lambda: completion("Truncated", finish="length"), "incomplete_output"),
    (lambda: httpx.Response(200, content=b"x" * 33000), "invalid_response"),
])
def test_bad_gateway_response_blocks_once_without_logging_content(setup, response, reason, capsys):
    build, _, _ = setup
    seen = []

    def handler(request):
        seen.append(request)
        return response()

    api = build(handler)
    _, child = queue(api)
    assert asyncio.run(api.app.state.portfolio_engine.run_once())
    assert not asyncio.run(api.app.state.portfolio_engine.run_once())
    actual = row(api, child["id"])
    assert len(seen) == 1 and actual["status"] == "blocked" and actual["runner_error"] == reason
    assert actual["runner_result"] is None
    if reason == "incomplete_output":
        assert actual["tokens"]["total"] == 26 and actual["tokens"]["source"] == "provider_reported"
    assert KEY not in capsys.readouterr().err


async def local_ollama_transport(request):
    """Translate the fixed alias to a real local model only inside opt-in tests."""
    port = int(os.environ["FACTORY_TEST_OLLAMA_PORT"])
    payload = json.loads(request.content)
    assert payload["model"] == "chat-sensitive"
    payload["model"] = "gemma4:e2b"
    payload["reasoning_effort"] = "none"
    payload.pop("metadata")
    async with httpx.AsyncClient(timeout=55, trust_env=False) as local:
        return await local.post(f"http://127.0.0.1:{port}/v1/chat/completions", json=payload)


@pytest.mark.skipif(not os.environ.get("FACTORY_TEST_OLLAMA_PORT"), reason="requires a running local Ollama")
def test_local_ollama_model_qualification(setup):
    build, _, _ = setup
    api = build(local_ollama_transport)
    _, child = queue(api)
    assert asyncio.run(api.app.state.portfolio_engine.run_once())
    result = row(api, child["id"])
    assert result["status"] == "done" and result["runner_result"].strip()
    assert result["tokens"]["total"] > 0 and result["tokens"]["source"] == "provider_reported"
