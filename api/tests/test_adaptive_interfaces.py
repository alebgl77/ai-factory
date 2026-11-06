"""Protected HTTP and live stdio protocol checks for the SDK's integration surfaces."""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ai_factory_workbench import Actor, Workbench
from ai_factory_workbench.approvals import RelayAuthority, sign_approval
from ai_factory_workbench.profiles import WorkProfile, get_profile
from app.main import create_app
from app.routers.workbench import configure_workbench_surfaces
from app.settings import Settings

API_DIR = Path(__file__).resolve().parents[1]
ROOT = "/api/v1/workbench"
PROXY = "10.89.255.2"


def _identity(user: str, group: str) -> dict[str, str]:
    return {"X-Forwarded-User": user, "X-Forwarded-Email": f"{user}@example.test", "X-Forwarded-Groups": group}


def _app(tmp_path):
    return create_app(Settings(state_dir=str(tmp_path), workbench_mode="protected_demo", auth_mode="proxy",
                               trusted_proxies=(f"{PROXY}/32",), role_map=("submit:editor", "approve:approver")))


def _proposal(subject: str, profile: WorkProfile | None = None) -> dict[str, Any]:
    profile = profile or get_profile()
    return {"subject": subject, "title": "AI model release review", "reason": "Review before enabling the model.",
            "evidence": {key: f"checked: {key}" for key in profile.required_evidence}}


def test_http_profile_review_and_companion_decision(tmp_path):
    app = _app(tmp_path)
    engine = Workbench(tmp_path / "isolated.sqlite", mode="protected_demo", workspace="platform-alpha")
    profile = WorkProfile.from_dict({**get_profile("platform").describe(), "id": "platform-alpha"})
    configure_workbench_surfaces(app, engine=engine, profile=profile)
    client = TestClient(app, client=(PROXY, 12345))
    proposer, reviewer = _identity("author", "submit"), _identity("auditor", "approve")
    assert client.get(f"{ROOT}/profiles").status_code == 401
    assert client.get(f"{ROOT}/profiles", headers=proposer).json()["active"]["id"] == profile.id
    assert client.get(f"{ROOT}/profiles", headers=proposer).json()["workspace"] == "platform-alpha"
    request = _proposal("governance-101", profile)
    rejected = client.post(f"{ROOT}/reviews", headers={**proposer, "Origin": "https://untrusted.test"}, json=request)
    assert rejected.status_code == 403
    assert client.post(f"{ROOT}/reviews", headers=proposer, json={**request, "workspace": "other"}).status_code == 422
    assert client.post(f"{ROOT}/reviews", headers=reviewer, json=request).status_code == 403
    created = client.post(f"{ROOT}/reviews", headers=proposer, json=request)
    assert created.status_code == 201, created.text
    case = created.json()
    assert case["profile"] == profile.snapshot()
    assert case["action"] is None
    url = f"{ROOT}/cases/{case['id']}"
    assert client.get(url, headers=reviewer).json()["proposal_hash"] == case["proposal_hash"]
    approval = {"decision": "approve", "revision": case["revision"], "proposal_hash": case["proposal_hash"],
                "rationale": "Independent evidence review completed."}
    assert client.post(f"{url}/decisions", headers=proposer, json=approval).status_code == 403
    assert client.post(f"{url}/decisions", headers=reviewer, json=approval).json()["status"] == "decided"
    assert engine.store.draft_count() == 0


@pytest.mark.parametrize("channel", ["slack", "teams", "service"])
def test_http_signed_relay_requires_configuration_auth_and_exact_proof(tmp_path, channel):
    app = _app(tmp_path)
    client = TestClient(app, client=(PROXY, 12345))
    proposer, caller = _identity("author", "submit"), _identity("workflow-relay", "submit")
    created = client.post(f"{ROOT}/reviews", headers=proposer, json=_proposal("governance-relay"))
    assert created.status_code == 201, created.text
    case = created.json()
    clock = app.state.workbench.clock
    now = int(clock().timestamp())
    key = bytes(range(32))
    actor = Actor("auditor", frozenset({"approver"}))
    authority = RelayAuthority(f"example-{channel}", key, channel, {"verified-reviewer": actor})
    envelope = {"workspace": app.state.workbench.workspace, "issuer": authority.issuer, "channel": channel,
                "identity": "verified-reviewer", "case_id": case["id"], "revision": case["revision"],
                "proposal_hash": case["proposal_hash"], "decision": "approve",
                "rationale": "  Evidence confirmed by the registered reviewer.  ", "issued_at": now,
                "expires_at": now + 60, "request_id": "approval-1"}
    body = {"envelope": envelope, "signature": sign_approval(key, envelope)}
    endpoint = f"{ROOT}/approval-relay"
    assert client.post(endpoint, headers=caller, json=body).status_code == 404
    configure_workbench_surfaces(app, authorities=(authority,))
    assert client.post(endpoint, json=body).status_code == 401
    assert client.post(endpoint, headers={**caller, "Origin": "https://untrusted.test"}, json=body).status_code == 403
    wrong = {**envelope, "workspace": "other"}
    assert client.post(endpoint, headers=caller,
                       json={"envelope": wrong, "signature": sign_approval(key, wrong)}).status_code == 403
    forged = {**body, "envelope": {**envelope, "rationale": "Tampered"}}
    response = client.post(endpoint, headers=caller, json=forged)
    assert response.status_code == 403
    assert "Tampered" not in response.text
    result = client.post(endpoint, headers=caller, json=body)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "decided"


def test_http_relay_cannot_be_configured_in_anonymous_demo(tmp_path):
    app = create_app(Settings(state_dir=str(tmp_path), workbench_mode="demo"))
    authority = RelayAuthority("internal", bytes(range(32)), "service",
                               {"reviewer": Actor("auditor", frozenset({"approver"}))})
    with pytest.raises(ValueError, match="proxy authentication"):
        configure_workbench_surfaces(app, authorities=(authority,))


def test_registered_machine_aliases_must_use_signed_service_approval(tmp_path):
    app = _app(tmp_path)
    machine = Actor("internal-system", frozenset({"approver"}), subject="machine-proxy",
                    username="SERVICE-ROBOT@example.test")
    authority = RelayAuthority("internal", bytes(range(32)), "service", {"workflow": machine},
                               service_identities=frozenset({"workflow"}))
    configure_workbench_surfaces(app, authorities=(authority,))
    client = TestClient(app, client=(PROXY, 12345))
    created = client.post(f"{ROOT}/reviews", headers=_identity("author", "submit"), json=_proposal("machine-guard"))
    assert created.status_code == 201, created.text
    case = created.json()
    url = f"{ROOT}/cases/{case['id']}"
    decision = {"decision": "approve", "revision": case["revision"], "proposal_hash": case["proposal_hash"],
                "rationale": "Independent review."}
    machine_aliases = (_identity("machine-proxy", "approve"),
                       {**_identity("other-proxy-user", "approve"), "X-Forwarded-Email": "service-robot@example.test"})
    for headers in machine_aliases:
        assert client.get(f"{ROOT}/cases", headers=headers).json()["capabilities"]["can_decide"] is False
        assert client.get(url, headers=headers).json()["can_decide"] is False
        denied = client.post(f"{url}/decisions", headers=headers, json=decision)
        assert denied.status_code == 403 and denied.json()["detail"] == "signed_service_approval_required"
    reviewer = _identity("auditor", "approve")
    assert client.get(url, headers=reviewer).json()["can_decide"] is True
    assert client.post(f"{url}/decisions", headers=reviewer, json=decision).json()["status"] == "decided"


def test_service_only_approves_fresh_operations_bound_draft(tmp_path):
    app = _app(tmp_path)
    engine = app.state.workbench
    key = bytes(range(32))
    machine = Actor("internal-system", frozenset({"approver"}))
    authority = RelayAuthority("internal", key, "service", {"workflow": machine},
                               service_identities=frozenset({"workflow"}))
    configure_workbench_surfaces(app, authorities=(authority,))
    client = TestClient(app, client=(PROXY, 12345))
    caller = _identity("workflow-relay", "submit")

    def opportunity(subject):
        return {"specversion": "1.0", "type": "crm.opportunity.won", "source": "crm-sim", "id": subject,
                "subject": subject, "data": {"title": "Order", "customer_name": "Atelier Acme",
                                             "customer_ref": "CRM-ACME", "amount_minor": 1900,
                                             "currency": "EUR", "record_version": 1}}

    def relay(case):
        now = int(engine.clock().timestamp())
        envelope = {"workspace": engine.workspace, "issuer": authority.issuer, "channel": "service",
                    "identity": "workflow", "case_id": case["id"], "revision": case["revision"],
                    "proposal_hash": case["proposal_hash"], "decision": "approve", "rationale": "Verified draft.",
                    "issued_at": now, "expires_at": now + 60, "request_id": case["id"]}
        return client.post(f"{ROOT}/approval-relay", headers=caller,
                           json={"envelope": envelope, "signature": sign_approval(key, envelope)})

    legacy = engine.submit(opportunity("legacy"), Actor("historical-author", frozenset({"editor"})))
    governance = client.post(f"{ROOT}/events", headers=caller, json=opportunity("governance")).json()
    assert governance["profile"] == get_profile().snapshot()
    assert relay(governance).json()["detail"] == "human_approval_required"
    configure_workbench_surfaces(app, profile="operations", authorities=(authority,))
    assert relay(legacy).json()["detail"] == "human_approval_required"
    assert relay(governance).json()["detail"] == "approval_profile_mismatch"
    assert engine.store.draft_count() == 0
    operational = client.post(f"{ROOT}/events", headers=caller, json=opportunity("operations")).json()
    assert operational["profile"] == get_profile("operations").snapshot()
    accepted = relay(operational)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "executed"
    assert engine.store.draft_count() == 1


def test_live_mcp_stdio_skill_discovery_and_proposal_only_tools(tmp_path):
    mcp = pytest.importorskip("mcp")
    from mcp.shared.exceptions import MCPError
    from mcp.types import PaginatedRequestParams, Request, RequestParams
    from pydantic import TypeAdapter

    class SkillLookup(RequestParams):
        uri: str

    async def scenario():
        database = tmp_path / "workspace.sqlite"
        args = ["-m", "ai_factory_workbench.mcp_server", "--database", str(database), "--workspace", "governance-a"]
        inherited = {key: os.environ[key] for key in ("PYTHONPATH", "PATH") if key in os.environ}
        parameters = mcp.StdioServerParameters(command=sys.executable, args=args, cwd=API_DIR, env=inherited)
        async with mcp.Client(parameters, read_timeout_seconds=12) as client:
            assert client.protocol_version == "2026-07-28"
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert set(tools) == {"workbench_get_profile", "workbench_list_cases", "workbench_get_case",
                                  "workbench_prepare_review", "workbench_propose_opportunity"}
            assert tools["workbench_get_profile"].annotations.read_only_hint is True
            assert tools["workbench_prepare_review"].annotations.open_world_hint is False
            assert "io.modelcontextprotocol/skills" in client.server_capabilities.extensions
            dictionary = TypeAdapter(dict[str, Any])
            catalog = await client.session.send_request(Request(method="skills/list", params=PaginatedRequestParams()),
                                                        dictionary)
            assert catalog["resultType"] == "complete"
            [skill] = catalog["skills"]
            uri = skill["uri"]
            entry = await client.session.send_request(Request(method="skills/get", params=SkillLookup(uri=uri)),
                                                      dictionary)
            assert entry["skill"] == skill
            assert [str(resource.uri) for resource in (await client.list_resources()).resources] == [uri]
            [resource] = (await client.read_resource(uri)).contents
            payload = resource.text.encode("utf-8")
            [manifest] = skill["resources"]
            assert manifest == {"uri": uri, "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
                                "size": len(payload)}
            assert f"description: {skill['frontmatter']['description']}" in resource.text
            with pytest.raises(MCPError):
                await client.read_resource("file:///secrets.txt")

            profile = await client.call_tool("workbench_get_profile")
            assert profile.structured_content["workspace"] == "governance-a"
            assert profile.structured_content["profile"]["id"] == "governance"
            proposal = _proposal("mcp-governance-1")
            invalid = await client.call_tool("workbench_prepare_review",
                                             {"proposal": {**proposal, "role": "untrusted-value"}})
            assert invalid.is_error
            assert "untrusted-value" not in invalid.model_dump_json()
            created = await client.call_tool("workbench_prepare_review", {"proposal": proposal})
            assert not created.is_error, created.content
            case = created.structured_content
            assert case["status"] == "waiting_review" and case["can_decide"] is False
            detail = await client.call_tool("workbench_get_case", {"case_id": case["id"]})
            assert detail.structured_content["proposal_hash"] == case["proposal_hash"]
            queue = await client.call_tool("workbench_list_cases", {"limit": 1})
            assert [item["id"] for item in queue.structured_content["cases"]] == [case["id"]]
            opportunity = {"id": "crm-delivery", "subject": "crm-opportunity",
                           "data": {"title": "Reference opportunity", "customer_name": "Unmapped customer",
                                    "amount_minor": 1900, "currency": "EUR", "record_version": 1}}
            recorded = await client.call_tool("workbench_propose_opportunity", {"event": opportunity})
            assert not recorded.is_error, recorded.content
            assert recorded.structured_content["status"] == "waiting_review"
            assert recorded.structured_content["can_decide"] is False
            assert recorded.structured_content["profile"] == get_profile().snapshot()
            queue = await client.call_tool("workbench_list_cases", {"limit": 2})
            top = await client.call_tool("workbench_list_cases", {"limit": 1})
            assert len(queue.structured_content["cases"]) == 2
            assert top.structured_content["cases"] == queue.structured_content["cases"][:1]
        engine = Workbench(database, workspace="governance-a")
        assert engine.store.draft_count() == 0

    asyncio.run(scenario())
