"""Protected one-shot assistance through the same private gateway as the runner."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import yaml

from app import assist, main
from app.portfolio_runner import MAX_TOKENS
from app.settings import Settings
from tests.test_bu_access import policy_file
from tests.test_portfolio import (BINDINGS, GLOBAL, GROUPS, OPS, OPS_MANAGER, OPS_PERSON, PORTFOLIO, PORTFOLIO_ROLES,
                                  REL, REL_AGENT, REL_MANAGER)
from tests.test_portfolio_runner import KEY, MASTER, WORK, completion, local_ollama_transport, setup
from tests.test_security import TRUSTED_PEER, who


API = "/api/v1/assist"
BODY = {"kind": "draft_task", "bu": REL, "title": "Draft fixture", "text": "Private fixture context"}
DECISION = {**BODY, "kind": "decision_outline", "source": "internal"}


def _build(setup, handler, **overrides):
    build, config, _ = setup
    return build(handler, settings=replace(config(), assist_enabled=True, **overrides))


def test_off_by_default_and_requires_live_bound_configuration(setup, monkeypatch):
    build, config, _ = setup
    api = build(lambda _request: pytest.fail("disabled route called gateway"))
    assert api.get(API, headers=REL_MANAGER).status_code == 404
    assert api.post(API, headers=REL_MANAGER, json=BODY).status_code == 404
    for invalid in ({"demo_mode": "auto"}, {"portfolio_runner_file": None}, {"rate_limit_per_min": 0}):
        with pytest.raises(ValueError, match="AI assistance requires"):
            replace(config(), assist_enabled=True, **invalid)
    monkeypatch.setenv("FACTORY_ASSIST_ENABLED", "unexpected")
    with pytest.raises(ValueError, match="invalid FACTORY_ASSIST_ENABLED"):
        Settings.from_env()


def test_human_bu_projection_role_split_and_machine_precedence(setup):
    api = _build(setup, lambda _request: pytest.fail("read or denied request called gateway"))
    assert api.get(API).status_code == 401
    for forbidden in (REL_AGENT, who("support-agent-subject", "relation-agent", "global-admin"),
                      who("split", "global-read", "finance-admin")):
        assert api.get(API, headers=forbidden).status_code == 403
        assert api.post(API, headers=forbidden, json=BODY).status_code == 403
    for granted in (GLOBAL, REL_MANAGER):
        response = api.get(API + "?lang=fr", headers=granted)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert [unit["id"] for unit in response.json()["units"]] == [REL]
    assert api.get(API, headers=OPS_PERSON).json()["units"] == []
    assert api.post(API, headers=OPS_MANAGER, json=BODY).status_code == 403
    assert api.post(API, headers=OPS_PERSON, json={**BODY, "bu": OPS}).status_code == 503


def test_verified_employee_needs_no_portfolio_role(setup):
    build, config, _ = setup
    settings = config()
    path = policy_file(Path(settings.bu_policy_path).parent,
                       groups={**GROUPS, "relation-reader": [REL]}, portfolio_roles=PORTFOLIO_ROLES,
                       portfolio_agents=BINDINGS, people={REL: ["p01"]}, identities={"alice": "p01"})
    seen = []

    def handler(request):
        seen.append(request)
        return completion()

    api = build(handler, settings=replace(
        settings, assist_enabled=True, bu_policy_path=path, role_map=(*settings.role_map, "relation-reader:viewer"),
    ))
    member = who("alice", "relation-reader")
    assert [unit["id"] for unit in api.get(API, headers=member).json()["units"]] == [REL]
    assert api.post(API, headers=member, json=BODY).status_code == 200
    denied = api.post(API, headers=member, json=DECISION)
    assert denied.status_code == 403 and denied.json() == {"detail": "assist_forbidden"}
    assert len(seen) == 1
    assert api.get(API, headers=who("not-rostered", "relation-reader")).status_code == 403


def test_decision_outline_requires_one_global_admin_grant(setup):
    api = _build(setup, lambda _request: pytest.fail("non-global decision request called gateway"))
    split_admin = who("split", "global-read", "finance-admin", "relation-manager")
    for human in (split_admin, REL_MANAGER):
        assert [unit["id"] for unit in api.get(API, headers=human).json()["units"]] == [REL]
        denied = api.post(API, headers=human, json=DECISION)
        assert denied.status_code == 403 and denied.json() == {"detail": "assist_forbidden"}


def test_decision_outline_requires_explicit_source(setup):
    api = _build(setup, lambda _request: pytest.fail("decision without explicit source called gateway"))
    response = api.post(API, headers=GLOBAL, json={**BODY, "kind": "decision_outline"})
    assert response.status_code == 422 and response.json() == {"detail": "invalid_assist_request"}


@pytest.mark.parametrize("kind", ["draft_task", "explain_process", "decision_outline"])
def test_exact_private_request_usage_no_writes_and_view(setup, kind):
    seen = []

    def handler(request):
        seen.append(request)
        return completion()

    api = _build(setup, handler)
    actor = GLOBAL if kind == "decision_outline" else REL_MANAGER
    payload = DECISION if kind == "decision_outline" else {**BODY, "kind": kind}
    response = api.post(API + "?lang=fr", headers=actor, json=payload)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    expected_view = api.get(PORTFOLIO + "?lang=fr", headers=actor).json()["view"]
    assert response.json() == {"text": WORK, "usage": {"input": 17, "output": 9, "total": 26,
                                                       "source": "provider_reported"}, "view": expected_view}
    request, = seen
    assert str(request.url) == "http://litellm:4000/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}" and MASTER not in request.headers.values()
    assert request.headers["accept-encoding"] == "identity"
    body = json.loads(request.content)
    assert set(body) == {"model", "stream", "max_tokens", "metadata", "messages"}
    assert body["model"] == "chat-sensitive" and body["metadata"] == {"usecase": "uc-02"}
    assert body["max_tokens"] == MAX_TOKENS and body["stream"] is False
    assert "French" in body["messages"][0]["content"] and "You have no tools" in body["messages"][0]["content"]
    assert json.loads(body["messages"][1]["content"]) == {"title": BODY["title"], "text": BODY["text"]}
    assert not api.app.state.portfolio.path.exists() and not hasattr(api.app.state, "workbench")


def test_public_source_is_a_plan_and_labels_no_research(setup):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content)["messages"][0]["content"])
        return completion()

    api = _build(setup, handler)
    response = api.post(API + "?lang=en", headers=GLOBAL, json={**BODY, "source": "public", "kind": "decision_outline"})
    assert response.status_code == 200
    assert response.json()["text"].startswith("No external research was performed.")
    instruction, = seen
    assert "plan for checking public sources" in instruction and "Do not supply purported findings" in instruction


@pytest.mark.parametrize("change", [{"source": "fetch"}, {"kind": "execute"}, {"provider": "redact-me"},
                                     {"text": "redact-me" + "x" * 2000}, {"text": "redact-me\u202e"}, {"text": " "}])
def test_bad_input_never_echoes_or_reaches_gateway(setup, change):
    api = _build(setup, lambda _request: pytest.fail("invalid request called gateway"))
    response = api.post(API, headers=REL_MANAGER, json={**BODY, **change})
    assert response.status_code == 422 and response.json() == {"detail": "invalid_assist_request"}


def test_origin_and_json_contract(setup):
    api = _build(setup, lambda _request: pytest.fail("rejected request called gateway"))
    assert api.get(API, headers={**REL_MANAGER, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert api.post(API, headers={**REL_MANAGER, "Origin": "https://foreign.invalid"}, json=BODY).status_code == 403
    assert api.get(API + "?lang=fr&lang=en", headers=REL_MANAGER).status_code == 422
    assert api.post(API, headers=REL_MANAGER, data=BODY).status_code == 415


@pytest.mark.parametrize(("make_response", "status", "detail"), [
    (lambda: httpx.Response(307, headers={"location": "http://redirect.invalid/secret"}), 503, "assist_unavailable"),
    (lambda: httpx.Response(500, text="gateway-private-diagnostic"), 503, "assist_unavailable"),
    (lambda: httpx.Response(200, content=b"x" * 32769), 503, "assist_unavailable"),
    (lambda: httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": WORK,
              "function_call": {"name": "execute"}}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
     503, "assist_unavailable"),
    (lambda: completion("", "length"), 502, "assist_incomplete"),
])
def test_gateway_failure_is_single_bounded_and_generic(setup, make_response, status, detail, capsys):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return make_response()

    api = _build(setup, handler)
    response = api.post(API, headers=REL_MANAGER, json=BODY)
    assert response.status_code == status and response.json() == {"detail": detail}
    assert calls == ["http://litellm:4000/v1/chat/completions"]
    logs = capsys.readouterr().err
    assert all(value not in logs for value in (KEY, MASTER, BODY["text"], WORK, "gateway-private-diagnostic"))


def test_revoked_active_binding_discards_in_flight_result(setup):
    build, config, _ = setup
    settings = replace(config(), assist_enabled=True)

    def handler(_request):
        catalogue = Path(settings.config_dir) / "catalog" / "agents.yaml"
        data = yaml.safe_load(catalogue.read_text(encoding="utf-8"))
        next(agent for agent in data["agents"] if agent["id"] == "ag-support-research")["eval_status"] = "failed"
        catalogue.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        return completion()

    api = build(handler, settings=settings)
    response = api.post(API, headers=REL_MANAGER, json=BODY)
    assert response.status_code == 403 and response.json() == {"detail": "assist_forbidden"}
    assert api.get(API, headers=REL_MANAGER).json()["units"] == []


def test_restricted_admin_grant_discards_in_flight_decision(setup):
    def handler(_request):
        service = api.app.state.service
        policy = service.bu_policy
        service.bu_policy = replace(policy, groups={**policy.groups, "global-admin": frozenset({REL})})
        return completion()

    api = _build(setup, handler)
    actor = who("direction", "global-admin", "relation-manager")
    response = api.post(API, headers=actor, json=DECISION)
    assert response.status_code == 403 and response.json() == {"detail": "assist_forbidden"}
    assert [unit["id"] for unit in api.get(API, headers=actor).json()["units"]] == [REL]


def test_absolute_timeout_and_write_throttle(setup, monkeypatch):
    monkeypatch.setattr(assist, "ASSIST_TIMEOUT", 0.02)

    async def handler(_request):
        await asyncio.sleep(1)
        return completion()

    api = _build(setup, handler, rate_limit_per_min=1, rate_limit_burst=1)
    response = api.post(API, headers=REL_MANAGER, json=BODY)
    assert response.status_code == 503 and response.json() == {"detail": "assist_unavailable"}
    assert api.post(API, headers=REL_MANAGER, json=BODY).status_code == 429


def test_concurrency_rejects_instead_of_queueing(setup, monkeypatch):
    monkeypatch.setattr(main, "ASSIST_CONCURRENCY", 1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(_request):
        entered.set()
        await release.wait()
        return completion()

    api = _build(setup, handler)

    async def verify():
        transport = httpx.ASGITransport(app=api.app, client=TRUSTED_PEER)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            first = asyncio.create_task(client.post(API, headers=REL_MANAGER, json=BODY))
            await asyncio.wait_for(entered.wait(), timeout=3)
            try:
                second = await client.post(API, headers=GLOBAL, json=BODY)
                assert second.status_code == 429 and second.json() == {"detail": "assist_busy"}
            finally:
                release.set()
            assert (await first).status_code == 200

    asyncio.run(verify())


@pytest.mark.skipif(not os.environ.get("FACTORY_TEST_OLLAMA_PORT"), reason="requires a running local Ollama")
def test_local_ollama_assist_qualification(setup):
    api = _build(setup, local_ollama_transport)
    response = api.post(API + "?lang=fr", headers=REL_MANAGER, json={
        **BODY, "title": "Contrôler une liste de test", "text": "Le livrable attendu est une liste de trois vérifications.",
    })
    assert response.status_code == 200, response.text
    output = response.json()
    assert output["text"].strip() and output["usage"]["total"] > 0
    assert output["usage"]["source"] == "provider_reported"
    assert not api.app.state.portfolio.path.exists()
