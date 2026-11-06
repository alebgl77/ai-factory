"""Security controls of the API: identity from the trusted proxy only, RBAC matrix, four-eyes,
hash-chained audit log (tamper detection, state reconciliation), rate limiting, headers, logs,
posture. Every identity and value here is fictional."""

from __future__ import annotations

import concurrent.futures
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.audit import GENESIS, AuditContext, record_hash, scan
from app.main import SWAGGER_UI_VERSION, create_app
from app.seclog import reference
from app.security import (ASSESSMENT, AUDIT_READ, AUTHENTICATED, MODEL_VERSION, PLACEMENT_DECIDE, READ, ROLES,
                          SPACES_WRITE, RateLimiter, SecurityConfig, roles_granting)
from app.settings import Settings
from app.state import SeparationOfDuties, StateStore, apply_registry_overrides
from tests.conftest import FIXTURE_DIR, NOW, TODAY, review_body

API_DIR = Path(__file__).resolve().parents[1]
TRUSTED_PEER = ("10.0.0.5", 41000)
UNTRUSTED_PEER = ("192.0.2.10", 41000)  # TEST-NET-1 (RFC 5737)
ROLE_MAP = ("grp-viewer:viewer", "grp-editor:editor", "grp-maintainer:maintainer", "grp-approver:approver",
            "grp-admin:admin")


def build(tmp_path: Path, peer: tuple[str, int] = TRUSTED_PEER, **overrides: Any) -> TestClient:
    params: dict[str, Any] = dict(config_dir=str(FIXTURE_DIR), demo_mode="always", demo_seed=42,
                                  demo_date=TODAY.isoformat(), state_dir=str(tmp_path / "state"), cache_ttl_s=0)
    params.update(overrides)
    return TestClient(create_app(Settings(**params), clock=lambda: NOW), client=peer)


def proxy(tmp_path: Path, peer: tuple[str, int] = TRUSTED_PEER, **overrides: Any) -> TestClient:
    params: dict[str, Any] = dict(auth_mode="proxy", trusted_proxies=("10.0.0.0/24",), role_map=ROLE_MAP)
    params.update(overrides)
    return build(tmp_path, peer, **params)


def who(user: str, *groups: str, email: str | None = None) -> dict[str, str]:
    headers = {"X-Forwarded-User": user, "X-Forwarded-Email": email or f"{user}@example.org"}
    if groups:
        headers["X-Forwarded-Groups"] = ",".join(groups)
    return headers


def audit_lines(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "state" / "audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# ------------------------------------------------------------------------------------ endpoints
# (method, path, json body, permission). Bodies are valid so an allowed call never fails on authz.
ENDPOINTS: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("GET", "/api/v1/snapshot", None, READ),
    ("GET", "/api/v1/registry", None, READ),
    ("GET", "/api/v1/registry/uc-01", None, READ),
    ("GET", "/api/v1/costs", None, READ),
    ("GET", "/api/v1/pipeline", None, READ),
    ("GET", "/api/v1/dataloop", None, READ),
    ("GET", "/api/v1/placement", None, READ),
    ("GET", "/api/v1/agentops", None, READ),
    ("GET", "/api/v1/catalog", None, READ),
    ("GET", "/api/v1/skills", None, READ),
    ("GET", "/api/v1/gpu", None, READ),
    ("GET", "/api/v1/grafana/dashboards", None, READ),
    ("GET", "/api/v1/grafana/metric/gpu_util", None, READ),
    ("GET", "/api/v1/spaces/state", None, READ),
    ("GET", "/api/v1/openapi.json", None, READ),
    ("GET", "/api/v1/docs", None, READ),
    ("GET", "/api/v1/audit", None, AUDIT_READ),
    ("GET", "/api/v1/audit/verify", None, AUDIT_READ),
    ("GET", "/api/v1/whoami", None, AUTHENTICATED),
    ("POST", "/api/v1/registry/uc-03/model-version", {"version": "2027-01"}, MODEL_VERSION),
    ("POST", "/api/v1/registry/uc-01/assessment", {"decision": "approved", "reviewer": "p07"}, ASSESSMENT),
    ("PUT", "/api/v1/spaces/state", {"version": 0, "state": {"bookings": []}}, SPACES_WRITE),
    ("POST", "/api/v1/placement/decide", {"sensitivity": "C1"}, PLACEMENT_DECIDE),
]
EXPECTED_PERMISSIONS = {(m, p): perm for m, p, _, perm in ENDPOINTS}


def call(client: TestClient, method: str, path: str, body: dict[str, Any] | None, headers: dict[str, str] | None = None,
         **kwargs: Any):
    if body is not None and path.endswith("/assessment"):
        proposal = client.get(path.removesuffix("/assessment"), headers=headers)
        revision = proposal.json()["review_revision"] if proposal.is_success else "0" * 64
        body = {**body, "expected_review_revision": revision}
    return client.request(method, path, json=body, headers=headers, **kwargs)


def _api_routes(routes) -> list[APIRoute]:
    """Every APIRoute, including those of included routers (kept lazily by recent FastAPI)."""
    out: list[APIRoute] = []
    for route in routes:
        if isinstance(route, APIRoute):
            out.append(route)
        elif hasattr(route, "original_router"):
            assert not getattr(route.include_context, "dependencies", None)  # no hidden include-level deps
            out += _api_routes(route.original_router.routes)
    return out


def _route_permissions(app) -> dict[tuple[str, str], str | None]:
    out: dict[tuple[str, str], str | None] = {}
    for route in _api_routes(app.routes):
        if not route.path.startswith("/api/"):
            continue
        perms = [getattr(d.call, "__factory_permission__", None) for d in route.dependant.dependencies]
        perms = [p for p in perms if p]
        for method in route.methods:
            out[(method, route.path)] = perms[0] if len(perms) == 1 else (None if not perms else "AMBIGUOUS")
    return out


def test_every_api_route_declares_exactly_one_permission(tmp_path):
    client = build(tmp_path)
    perms = _route_permissions(client.app)
    unprotected = sorted(k for k, v in perms.items() if v is None and k[1] != "/api/v1/health")
    assert not unprotected, f"routes without a permission: {unprotected}"
    assert perms[("GET", "/api/v1/health")] is None  # public liveness probe
    assert "AMBIGUOUS" not in perms.values()
    expected = {(m, re.sub(r"uc-0\d", "{uc_id}", p).replace("gpu_util", "{name}")): perm
                for (m, p), perm in EXPECTED_PERMISSIONS.items()}
    assert {k: v for k, v in perms.items() if k[1] != "/api/v1/health"} == expected


def test_role_permission_table_keeps_duties_separate():
    assert roles_granting(MODEL_VERSION) == ["maintainer", "admin"]
    assert roles_granting(ASSESSMENT) == ["approver", "admin"]
    assert roles_granting(SPACES_WRITE) == roles_granting(PLACEMENT_DECIDE) == ["editor", "admin"]
    assert roles_granting(AUDIT_READ) == ["approver", "admin"]
    assert roles_granting(READ) == list(ROLES)


# ------------------------------------------------------------------------------------ mode none
def test_none_mode_ignores_identity_headers_and_flags_the_posture(tmp_path):
    client = build(tmp_path)
    me = client.get("/api/v1/whoami", headers=who("mallory", "grp-viewer")).json()
    assert me["actor"] == "anonymous" and me["authenticated"] is False and me["roles"] == ["admin"]
    sec = client.get("/api/v1/health").json()["security"]
    assert sec["status"] == "demo" and sec["auth_mode"] == "none" and sec["rbac"] == "not_enforced"
    assert "auth_disabled" in sec["warnings"] and sec["separation_of_duties"] == "not_enforced"


@pytest.mark.parametrize("method,path,body,perm", ENDPOINTS)
def test_none_mode_allows_everything_as_admin(tmp_path, method, path, body, perm):
    r = call(build(tmp_path), method, path, body, headers=who("mallory"))
    assert r.status_code not in (401, 403, 429), (path, r.text)


# ------------------------------------------------------------------------------------ mode proxy
@pytest.mark.parametrize("method,path,body,perm", ENDPOINTS)
def test_proxy_mode_untrusted_source_is_rejected_even_with_identity_headers(tmp_path, method, path, body, perm):
    client = proxy(tmp_path, peer=UNTRUSTED_PEER)
    r = call(client, method, path, body, headers=who("admin", "grp-admin"))
    assert r.status_code == 401 and r.json()["code"] == "untrusted_source", (path, r.text)
    assert r.headers["www-authenticate"].startswith("SSO ")


def test_proxy_mode_unknown_api_path_is_denied_by_default(tmp_path):
    assert proxy(tmp_path, peer=UNTRUSTED_PEER).get("/api/v1/does-not-exist").status_code == 401
    assert proxy(tmp_path).get("/api/v1/does-not-exist").status_code == 401  # trusted, no identity


def test_proxy_mode_health_stays_public(tmp_path):
    r = proxy(tmp_path, peer=UNTRUSTED_PEER).get("/api/v1/health")
    assert r.status_code == 200 and r.json()["security"]["status"] == "enforced"


@pytest.mark.parametrize("role", [*ROLES, None])
@pytest.mark.parametrize("method,path,body,perm", ENDPOINTS)
def test_proxy_mode_authorization_matrix(tmp_path, role, method, path, body, perm):
    client = proxy(tmp_path)
    headers = who(f"user-{role or 'none'}", *([f"grp-{role}"] if role else ["grp-unmapped"]))
    r = call(client, method, path, body, headers=headers)
    allowed = perm == AUTHENTICATED or (role is not None and role in roles_granting(perm))
    if allowed:
        assert r.status_code not in (401, 403, 429), (role, path, r.text)
    else:
        assert r.status_code == 403 and r.json()["code"] == "forbidden", (role, path, r.text)
        assert perm in r.json()["detail"]


def test_proxy_mode_trusted_peer_without_identity_is_401(tmp_path):
    r = proxy(tmp_path).get("/api/v1/snapshot", headers={"X-Forwarded-Groups": "grp-admin"})
    assert r.status_code == 401 and r.json()["code"] == "unauthenticated"
    r = proxy(tmp_path).get("/api/v1/snapshot", headers={"X-Forwarded-User": "  "})
    assert r.status_code == 401 and r.json()["code"] == "unauthenticated"


def test_proxy_mode_rejects_ambiguous_or_malformed_identity(tmp_path):
    client = proxy(tmp_path)
    r = client.get("/api/v1/snapshot", headers=[("X-Forwarded-User", "alice"), ("X-Forwarded-User", "admin"),
                                                 ("X-Forwarded-Groups", "grp-admin")])
    assert r.status_code == 401 and r.json()["code"] == "invalid_identity"
    r = client.get("/api/v1/snapshot", headers={"X-Forwarded-User": "a" * 300, "X-Forwarded-Groups": "grp-admin"})
    assert r.status_code == 401 and r.json()["code"] == "invalid_identity"
    r = client.get("/api/v1/snapshot", headers={"X-Forwarded-User": "alice",
                                                 "X-Forwarded-Groups": ",".join(f"g{i}" for i in range(101))})
    assert r.status_code == 401 and r.json()["code"] == "invalid_identity"


def test_proxy_mode_messages_are_localised(tmp_path):
    client = proxy(tmp_path)
    r = client.get("/api/v1/snapshot", params={"lang": "fr"})
    assert r.json()["detail"].startswith("authentification requise")
    r = client.get("/api/v1/audit", params={"lang": "fr"}, headers=who("vera", "grp-viewer"))
    assert r.status_code == 403 and r.json()["detail"].startswith("accès refusé : la permission audit.read")
    r = client.get("/api/v1/audit", headers=who("vera", "grp-viewer"))
    assert r.json()["detail"] == "access denied: the audit.read permission requires one of these roles: approver, admin"
    r = proxy(tmp_path, peer=UNTRUSTED_PEER).get("/api/v1/snapshot", params={"lang": "fr"})
    assert r.json()["detail"].startswith("requête refusée")


def test_proxy_mode_default_role_and_multiple_groups(tmp_path):
    client = proxy(tmp_path, default_role="viewer")
    assert client.get("/api/v1/snapshot", headers=who("nina")).status_code == 200
    assert client.put("/api/v1/spaces/state", json={"version": 0, "state": {}}, headers=who("nina")).status_code == 403
    me = client.get("/api/v1/whoami", headers=who("max", "grp-editor", "grp-maintainer")).json()
    assert me["roles"] == ["editor", "maintainer", "viewer"]
    assert set(me["permissions"]) == {READ, SPACES_WRITE, PLACEMENT_DECIDE, MODEL_VERSION}


def test_proxy_mode_without_role_mapping_denies_everyone(tmp_path):
    client = proxy(tmp_path, role_map=())
    assert client.get("/api/v1/snapshot", headers=who("alice", "grp-admin")).status_code == 403
    assert "no_role_mapping" in client.get("/api/v1/health").json()["security"]["warnings"]


@pytest.mark.parametrize("overrides,warning", [
    ({"auth_mode": "oidc"}, "auth_mode_invalid"),
    ({"trusted_proxies": ("0.0.0.0/0",)}, "trusted_proxy_any_refused"),
    ({"trusted_proxies": ("::/0",)}, "trusted_proxy_any_refused"),
    ({"trusted_proxies": ("not-a-cidr",)}, "trusted_proxy_invalid"),
    ({"trusted_proxies": ()}, "no_trusted_proxy"),
])
def test_misconfiguration_fails_closed(tmp_path, overrides, warning):
    client = proxy(tmp_path, peer=TRUSTED_PEER, **{"trusted_proxies": ("10.0.0.0/24",), **overrides})
    sec = client.get("/api/v1/health").json()["security"]
    assert warning in sec["warnings"] and sec["auth_mode"] == "proxy" and sec["status"] == "degraded"
    if "trusted_proxies" in overrides:  # nobody can assert an identity any more
        assert client.get("/api/v1/snapshot", headers=who("a", "grp-admin")).status_code == 401


def test_security_config_parsing():
    cfg = SecurityConfig.from_settings(Settings(auth_mode="proxy", trusted_proxies=("10.0.0.1", "fd00::/8", "x"),
                                                role_map=("a:approver", "b:root", "nocolon", "role:x:editor"),
                                                default_role="superuser"))
    assert [str(n) for n in cfg.trusted_networks] == ["10.0.0.1/32", "fd00::/8"]
    assert cfg.role_map == (("a", "approver"), ("role:x", "editor"))  # group names may contain ':'
    assert cfg.default_role is None
    assert {"trusted_proxy_invalid", "role_map_invalid", "default_role_invalid"} <= set(cfg.warnings)
    assert cfg.trusts("::ffff:10.0.0.1") and not cfg.trusts("10.0.0.2") and not cfg.trusts("testclient")


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("FACTORY_AUTH_MODE", "PROXY")
    monkeypatch.setenv("FACTORY_TRUSTED_PROXIES", " 172.20.0.0/16 , 10.1.2.3 ")
    monkeypatch.setenv("FACTORY_ROLE_MAP", "ai-governance:approver,ai-platform:maintainer,staff:editor")
    monkeypatch.setenv("FACTORY_DEFAULT_ROLE", "Viewer")
    monkeypatch.setenv("FACTORY_RATE_LIMIT_PER_MIN", "30")
    monkeypatch.setenv("FACTORY_RATE_LIMIT_BURST", "5")
    s = Settings.from_env()
    assert s.auth_mode == "proxy" and s.trusted_proxies == ("172.20.0.0/16", "10.1.2.3")
    assert s.role_map == ("ai-governance:approver", "ai-platform:maintainer", "staff:editor")
    assert s.default_role == "viewer" and s.rate_limit_per_min == 30 and s.rate_limit_burst == 5
    monkeypatch.delenv("FACTORY_AUTH_MODE")
    assert Settings.from_env().auth_mode == "none"


# ------------------------------------------------------------------------------------ four-eyes
def test_four_eyes_refuses_self_approval_and_records_the_reviewer(tmp_path):
    client = proxy(tmp_path)
    alice = who("alice", "grp-maintainer", "grp-approver")  # holds both duties on purpose
    assert client.post("/api/v1/registry/uc-02/model-version", json={"version": "2026-11"},
                       headers=alice).status_code == 200
    body = review_body(client, "uc-02", headers=alice)
    r = client.post("/api/v1/registry/uc-02/assessment", json=body, headers=alice)
    assert r.status_code == 403 and r.json()["code"] == "separation_of_duties"
    assert "2026-11" in r.json()["detail"]
    fr = client.post("/api/v1/registry/uc-02/assessment", params={"lang": "fr"},
                     json={**body, "decision": "approved_with_conditions"}, headers=alice)
    assert fr.status_code == 403 and fr.json()["detail"].startswith("séparation des tâches")
    # nothing changed: the deployment gate stays blocked
    assert client.get("/api/v1/registry/uc-02", headers=alice).json()["risk"]["reevaluation_required"] is True
    # the same person, through another account carrying the same e-mail, is still refused
    r = client.post("/api/v1/registry/uc-02/assessment", json=body,
                    headers=who("alice.admin", "grp-approver", email="ALICE@example.org"))
    assert r.status_code == 403
    # rejecting or keeping pending stays possible (the gate stays closed)
    r = client.post("/api/v1/registry/uc-02/assessment", json={**body, "decision": "pending"},
                    headers=alice)
    assert r.status_code == 200 and r.json()["assessment"]["separation_of_duties"] == "not_required"
    # an independent approver can approve; the authenticated reviewer is recorded
    r = client.post("/api/v1/registry/uc-02/assessment", json=body,
                    headers=who("bob", "grp-approver"))
    assert r.status_code == 200, r.text
    a = r.json()["assessment"]
    assert a["reviewed_by"] == "bob@example.org" and a["reviewed_by_role"] == "approver"
    assert a["separation_of_duties"] == "verified" and r.json()["reevaluation_required"] is False
    denied = [x for x in audit_lines(tmp_path) if x["outcome"] == "denied"]
    assert len(denied) == 3 and all(x["details"]["reason"] == "separation_of_duties" for x in denied)
    assert denied[0]["actor"] == "alice@example.org" and denied[0]["before_hash"] == denied[0]["after_hash"]


def test_four_eyes_version_defined_in_git_has_no_api_recorder(tmp_path):
    client = proxy(tmp_path)
    bob = who("bob", "grp-approver")
    r = client.post("/api/v1/registry/uc-01/assessment", json=review_body(client, "uc-01", headers=bob), headers=bob)
    assert r.status_code == 200 and r.json()["assessment"]["separation_of_duties"] == "unknown_recorder"


def test_four_eyes_not_enforced_in_demo_mode_but_recorded(tmp_path):
    client = build(tmp_path)
    client.post("/api/v1/registry/uc-02/model-version", json={"version": "2026-11"})
    r = client.post("/api/v1/registry/uc-02/assessment", json=review_body(client, "uc-02"))
    assert r.status_code == 200 and r.json()["assessment"]["separation_of_duties"] == "not_enforced"
    assert r.json()["assessment"]["reviewed_by"] == "anonymous"


def test_four_eyes_is_checked_under_the_state_lock(tmp_path, cfg):
    store = StateStore(str(tmp_path / "s"))
    uc = next(u for u in cfg["usecases"] if u["id"] == "uc-02")
    alice = AuditContext(actor="alice@example.org", role="maintainer", auth_mode="proxy", request_id="r1",
                         client_ip="10.0.0.9", ids=frozenset({"alice", "alice@example.org"}), authenticated=True)
    store.set_model_version(uc, "2026-11", None, TODAY, audit=alice)
    proposal = apply_registry_overrides([uc], store.registry_overrides())[0]
    with pytest.raises(SeparationOfDuties):
        store.record_assessment(uc, "approved", "p07", TODAY, expected_review_revision=proposal["review_revision"],
                                audit=alice, enforce_four_eyes=True)


def test_stale_assessment_cannot_approve_new_or_reused_model_version(tmp_path):
    client = proxy(tmp_path)
    maintainer, approver = who("maya", "grp-maintainer"), who("amir", "grp-approver")
    path = "/api/v1/registry/uc-02"
    old = review_body(client, "uc-02", headers=approver)
    original_version = client.get(path, headers=approver).json()["model"]["version"]
    proposal = client.post(f"{path}/model-version", json={"version": "2026-11", "name": "private-model-label"},
                           headers=maintainer)
    assert proposal.status_code == 200
    overlay = tmp_path / "state" / "registry_overrides.json"
    before = overlay.read_bytes()
    stale = client.post(f"{path}/assessment", params={"lang": "fr"}, json=old, headers=approver)
    assert stale.status_code == 409 and stale.json()["code"] == "stale_review_revision"
    assert stale.json()["detail"].startswith("la proposition a changé")
    assert overlay.read_bytes() == before
    denied = audit_lines(tmp_path)[-1]
    assert denied["action"] == "registry.assessment" and denied["outcome"] == "denied"
    assert denied["before_hash"] == denied["after_hash"]
    assert denied["details"] == {"decision": "approved", "reason": "stale_review_revision"}
    assert "private-model-label" not in stale.text
    assert "private-model-label" not in json.dumps(denied)
    assert old["expected_review_revision"] not in json.dumps(denied)
    assert client.get(path, headers=approver).json()["pipeline"]["blocked_by"] == "risk_reassessment"

    returned = client.post(f"{path}/model-version", json={"version": original_version}, headers=maintainer)
    assert returned.status_code == 200
    assert returned.json()["review_revision"] != old["expected_review_revision"]
    assert client.post(f"{path}/assessment", json=old, headers=approver).status_code == 409
    fresh = review_body(client, "uc-02", headers=approver)
    approved = client.post(f"{path}/assessment", json=fresh, headers=approver)
    assert approved.status_code == 200
    assert approved.json()["assessment"]["reviewed_revision"] == fresh["expected_review_revision"]
    assert approved.json()["deploy_gate"] == "open"
    assert client.get("/api/v1/audit/verify", headers=approver).json()["ok"] is True


# ------------------------------------------------------------------------------------ audit log
def test_every_write_is_audited_with_a_valid_chain(tmp_path):
    client = proxy(tmp_path)
    m, a, e = who("maya", "grp-maintainer"), who("amir", "grp-approver"), who("eli", "grp-editor")
    rid = "req-0001-abc"
    assert client.post("/api/v1/registry/uc-02/model-version", json={"version": "2026-11", "name": "Secret name"},
                       headers={**m, "X-Request-ID": rid}).status_code == 200
    assert client.post("/api/v1/registry/uc-02/assessment", json=review_body(client, "uc-02", headers=a),
                       headers=a).status_code == 200
    assert client.put("/api/v1/spaces/state", json={"version": 0, "state": {"bookings": [{"note": "BODY-SENTINEL"}]}},
                      headers=e).status_code == 200
    assert client.post("/api/v1/placement/decide", json={"usecase": "uc-04", "targets": {"local": "down"}},
                       headers=e).status_code == 200
    lines = audit_lines(tmp_path)
    assert [x["action"] for x in lines] == ["registry.model_version", "registry.assessment", "spaces.put",
                                            "placement.decide"]
    assert [x["seq"] for x in lines] == [1, 2, 3, 4]
    assert lines[0]["prev_hash"] == GENESIS and all(lines[i]["prev_hash"] == lines[i - 1]["hash"] for i in (1, 2, 3))
    for x in lines:
        assert {"ts", "actor", "role", "action", "target", "before_hash", "after_hash", "request_id", "client_ip",
                "prev_hash", "hash", "outcome"} <= set(x)
        assert record_hash(x) == x["hash"] and x["client_ip"] == "10.0.0.5"
    assert lines[0]["actor"] == "maya@example.org" and lines[0]["role"] == "maintainer"
    assert lines[0]["request_id"] == rid and lines[0]["target"] == "usecase:uc-02"
    assert lines[0]["before_hash"] is None and len(lines[0]["after_hash"]) == 64
    assert lines[1]["before_hash"] == lines[0]["after_hash"]  # the chain of states per target
    assert lines[3]["details"] == {"usecase": "uc-04", "sensitivity": "C3", "target": "queued", "simulated": True}
    raw = (tmp_path / "state" / "audit.jsonl").read_text()
    assert "BODY-SENTINEL" not in raw and "Secret name" not in raw  # never full bodies or free text
    v = client.get("/api/v1/audit/verify", headers=a).json()
    assert v["ok"] is True and v["records"] == 4 and v["first_broken"] is None and v["head_hash"] == lines[-1]["hash"]
    assert v["state"] == {"consistent": True, "drift": [], "unaudited": []}


def test_audit_listing_is_paginated_newest_first(tmp_path):
    client = proxy(tmp_path)
    e, a = who("eli", "grp-editor"), who("amir", "grp-approver")
    for _ in range(5):
        client.post("/api/v1/placement/decide", json={"sensitivity": "C1"}, headers=e)
    client.put("/api/v1/spaces/state", json={"version": 0, "state": {}}, headers=e)
    page = client.get("/api/v1/audit", params={"limit": 2, "offset": 1}, headers=a)
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    body = page.json()
    assert body["total"] == 6 and body["order"] == "desc" and [r["seq"] for r in body["records"]] == [5, 4]
    only = client.get("/api/v1/audit", params={"action": "spaces.put"}, headers=a).json()
    assert only["total"] == 1 and only["records"][0]["action"] == "spaces.put"
    assert client.get("/api/v1/audit", params={"limit": 501}, headers=a).status_code == 422
    assert client.get("/api/v1/audit", params={"action": "rm -rf"}, headers=a).status_code == 422


def _three_records(tmp_path: Path) -> tuple[TestClient, Path]:
    client = build(tmp_path)
    client.post("/api/v1/registry/uc-02/model-version", json={"version": "2026-11"})
    client.put("/api/v1/spaces/state", json={"version": 0, "state": {"a": 1}})
    client.post("/api/v1/placement/decide", json={"sensitivity": "C2"})
    return client, tmp_path / "state" / "audit.jsonl"


def _edit_actor(lines: list[str]) -> list[str]:
    rec = json.loads(lines[1])
    rec["actor"] = "someone-else"
    return [lines[0], json.dumps(rec), lines[2]]


def _edit_and_rehash(lines: list[str]) -> list[str]:
    rec = json.loads(lines[1])
    rec["details"]["version"] = 99
    rec["hash"] = record_hash(rec)  # a careful forger recomputes the hash of the edited line...
    return [lines[0], json.dumps(rec), lines[2]]


@pytest.mark.parametrize("tamper,line,reason", [
    (_edit_actor, 2, "hash_mismatch"),
    (_edit_and_rehash, 3, "prev_hash_mismatch"),  # ...which breaks the link of the next one
    (lambda ls: [ls[0], ls[2]], 2, "sequence_mismatch"),            # a deleted line
    (lambda ls: [ls[1], ls[0], ls[2]], 1, "sequence_mismatch"),     # reordered lines
    (lambda ls: [ls[0], "{not json", ls[1], ls[2]], 2, "unparseable"),
    (lambda ls: [ls[0], ls[1], ls[2][: len(ls[2]) // 2]], 3, "truncated"),
])
def test_tampering_is_detected(tmp_path, tamper, line, reason):
    client, path = _three_records(tmp_path)
    assert client.get("/api/v1/audit/verify").json()["ok"] is True
    original = path.read_text().splitlines()
    tampered = tamper(original)
    text = "\n".join(tampered) + ("" if reason == "truncated" else "\n")
    path.write_text(text)
    v = client.get("/api/v1/audit/verify").json()
    assert v["ok"] is False and v["chain_ok"] is False
    assert v["first_broken"]["line"] == line and v["first_broken"]["reason"] == reason, v
    sec = client.get("/api/v1/health").json()["security"]
    assert sec["audit"]["chain_ok"] is False and "audit_chain_broken" in sec["warnings"]
    assert client.get("/api/v1/snapshot").json()["meta"]["security"]["audit"]["chain_ok"] is False
    assert "factory_audit_chain_ok 0.0" in client.get("/metrics").text
    # writes continue (availability) and stay chained to the stored head; the break stays reported
    assert client.post("/api/v1/placement/decide", json={"sensitivity": "C1"}).status_code == 200
    after = client.get("/api/v1/audit/verify").json()
    assert after["first_broken"]["line"] == line and after["records"] == v["records"] + 1


def test_offline_verifier_cli(tmp_path):
    _, path = _three_records(tmp_path)
    cmd = [sys.executable, "-m", "app.audit", "verify", str(path)]
    ok = subprocess.run(cmd, cwd=API_DIR, capture_output=True, text=True, check=False)
    assert ok.returncode == 0 and json.loads(ok.stdout)["chain_ok"] is True
    path.write_text(path.read_text().replace('"role":"admin"', '"role":"viewer"', 1))
    ko = subprocess.run(cmd, cwd=API_DIR, capture_output=True, text=True, check=False)
    assert ko.returncode == 1 and json.loads(ko.stdout)["first_broken"]["reason"] == "hash_mismatch"


def test_state_changed_outside_the_api_is_reported_as_drift(tmp_path):
    client, _ = _three_records(tmp_path)
    reg = tmp_path / "state" / "registry_overrides.json"
    data = json.loads(reg.read_text())
    data["uc-02"]["assessment"]["decision"] = "approved"  # silent approval, bypassing the API
    reg.write_text(json.dumps(data))
    v = client.get("/api/v1/audit/verify").json()
    assert v["chain_ok"] is True and v["ok"] is False
    assert v["state"]["drift"] == ["usecase:uc-02"] and v["state"]["consistent"] is False


def test_state_written_before_the_audit_existed_is_listed_as_unaudited(tmp_path, cfg):
    StateStore(str(tmp_path / "state")).set_model_version(
        next(u for u in cfg["usecases"] if u["id"] == "uc-03"), "2026-12", None, TODAY)  # no audit context
    v = build(tmp_path).get("/api/v1/audit/verify").json()
    assert v["ok"] is True and v["state"]["unaudited"] == ["usecase:uc-03"]


def test_no_unaudited_write_when_the_audit_log_cannot_be_written(tmp_path):
    client = build(tmp_path)
    assert client.get("/api/v1/snapshot").status_code == 200
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "audit.jsonl").mkdir()  # the log cannot be opened for writing
    r = client.post("/api/v1/registry/uc-02/model-version", json={"version": "2026-11"})
    assert r.status_code == 503 and r.json()["detail"] == "audit log unavailable: the change was not saved"
    assert not (tmp_path / "state" / "registry_overrides.json").exists()  # rolled back
    assert client.get("/api/v1/registry/uc-02").json()["model"]["version"] != "2026-11"
    assert client.put("/api/v1/spaces/state", json={"version": 0, "state": {}}).status_code == 503
    assert client.get("/api/v1/spaces/state").json()["version"] == 0
    assert client.post("/api/v1/placement/decide", json={"sensitivity": "C1"}).status_code == 503
    sec = client.get("/api/v1/health").json()["security"]
    assert sec["audit"]["available"] is False and "audit_unavailable" in sec["warnings"]


def test_concurrent_writers_keep_one_valid_chain(tmp_path, cfg):
    stores = [StateStore(str(tmp_path / "s")) for _ in range(3)]  # three "processes" on one volume
    ctx = AuditContext(actor="svc", role="admin", auth_mode="none", request_id="r", client_ip="127.0.0.1")
    uc = next(u for u in cfg["usecases"] if u["id"] == "uc-02")

    def work(i: int) -> None:
        store = stores[i % 3]
        if i % 2:
            store.append_audit(ctx, action="placement.decide", target="placement:what-if", after={"i": i})
        else:
            store.set_model_version(uc, f"v{i}", None, TODAY, audit=ctx)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(work, range(40)))
    status = scan((tmp_path / "s" / "audit.jsonl").read_bytes())
    assert status.ok and status.records == 40 and status.last_seq == 40
    assert stores[0].verify_audit()["state"]["consistent"] is True


# ------------------------------------------------------------------------------------ rate limit
def test_writes_are_rate_limited_per_actor_with_retry_after(tmp_path):
    client = proxy(tmp_path, rate_limit_per_min=6, rate_limit_burst=3)
    eli, ana = who("eli", "grp-editor"), who("ana", "grp-editor")
    codes = [client.post("/api/v1/placement/decide", json={"sensitivity": "C1"}, headers=eli).status_code
             for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = client.post("/api/v1/placement/decide", params={"lang": "fr"}, json={"sensitivity": "C1"}, headers=eli)
    assert r.status_code == 429 and r.json()["code"] == "rate_limited"
    assert int(r.headers["retry-after"]) >= 1 and r.json()["detail"].startswith("trop de requêtes")
    assert client.post("/api/v1/placement/decide", json={"sensitivity": "C1"}, headers=ana).status_code == 200
    assert all(client.get("/api/v1/placement", headers=eli).status_code == 200 for _ in range(10))  # reads: no limit
    assert len(audit_lines(tmp_path)) == 4  # refused writes leave no audit record


def test_rate_limit_keys_on_client_ip_without_authentication(tmp_path):
    client = build(tmp_path, rate_limit_per_min=60, rate_limit_burst=2)
    assert [client.put("/api/v1/spaces/state", json={"version": i, "state": {}}).status_code
            for i in range(3)] == [200, 200, 429]


def test_token_bucket_refills_and_is_bounded():
    now = [0.0]
    limiter = RateLimiter(per_minute=60, burst=2, clock=lambda: now[0], max_keys=3)
    assert limiter.take("a") == 0 and limiter.take("a") == 0
    assert limiter.take("a") == pytest.approx(1.0)
    now[0] += 1.0
    assert limiter.take("a") == 0
    for key in "bcde":
        limiter.take(key)
    assert len(limiter._buckets) == 3  # memory stays bounded
    assert RateLimiter(per_minute=0, burst=1).take("x") == 0  # disabled


def test_rate_limit_can_be_disabled_and_is_reported(tmp_path):
    client = build(tmp_path, rate_limit_per_min=0)
    sec = client.get("/api/v1/health").json()["security"]
    assert sec["rate_limit"]["enabled"] is False and "rate_limit_disabled" in sec["warnings"]
    assert all(client.post("/api/v1/placement/decide", json={"sensitivity": "C1"}).status_code == 200
               for _ in range(30))


# ------------------------------------------------------------------------------------ headers, ids, logs
@pytest.mark.parametrize("method,path,kwargs,status", [
    ("GET", "/api/v1/health", {}, 200),
    ("GET", "/api/v1/snapshot", {}, 200),
    ("GET", "/api/v1/registry/uc-99", {}, 404),
    ("PUT", "/api/v1/spaces/state", {"content": b"x" * 70000, "headers": {"content-type": "application/json"}}, 413),
    ("POST", "/api/v1/placement/decide", {"json": {"sensitivity": "C1"}}, 200),
    ("GET", "/api/v1/audit", {}, 200),
])
def test_security_headers_on_every_api_response(tmp_path, method, path, kwargs, status):
    r = build(tmp_path).request(method, path, **kwargs)
    assert r.status_code == status
    h = r.headers
    assert h["x-content-type-options"] == "nosniff" and h["cache-control"] == "no-store"
    assert h["content-security-policy"].startswith("default-src 'none'") and "frame-ancestors 'none'" in \
        h["content-security-policy"]
    assert h["referrer-policy"] == "no-referrer" and h["x-frame-options"] == "DENY"
    assert h["cross-origin-resource-policy"] == "same-origin"
    assert re.fullmatch(r"[0-9a-f]{32}", h["x-request-id"])


def test_headers_also_on_denials_and_metrics(tmp_path):
    r = proxy(tmp_path, peer=UNTRUSTED_PEER).get("/api/v1/snapshot")
    assert r.status_code == 401 and r.headers["x-content-type-options"] == "nosniff" and r.headers["x-request-id"]
    m = build(tmp_path).get("/metrics")
    assert m.headers["x-content-type-options"] == "nosniff" and "cache-control" not in m.headers


def test_request_id_is_propagated_or_replaced(tmp_path):
    client = build(tmp_path)
    assert client.get("/api/v1/health", headers={"X-Request-ID": "trace-42.a:b"}).headers["x-request-id"] == \
        "trace-42.a:b"
    for bad in ("has space", "x" * 200, "<script>", "été"):
        rid = client.get("/api/v1/health", headers={"X-Request-ID": bad.encode("utf-8")}).headers["x-request-id"]
        assert re.fullmatch(r"[0-9a-f]{32}", rid), bad


def test_docs_page_is_pinned_with_sri_and_a_nonce(tmp_path):
    r = build(tmp_path).get("/api/v1/docs")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    nonce = re.search(r"'nonce-([A-Za-z0-9_-]+)'", r.headers["content-security-policy"]).group(1)
    assert r.text.count(f'nonce="{nonce}"') == 2
    assert f"swagger-ui-dist@{SWAGGER_UI_VERSION}/" in r.text and "@5/" not in r.text
    assert r.text.count('integrity="sha384-') == 2 and 'crossorigin="anonymous"' in r.text
    assert "'unsafe-inline'" not in r.headers["content-security-policy"].split("style-src")[0]
    assert build(tmp_path).get("/api/v1/docs").headers["content-security-policy"] != \
        r.headers["content-security-policy"]  # a fresh nonce per answer


def test_denials_are_logged_as_json_without_spoofed_values(tmp_path, capsys):
    client = proxy(tmp_path, peer=UNTRUSTED_PEER)
    capsys.readouterr()
    client.get("/api/v1/snapshot", headers={**who("mallory-spoofed", "grp-admin"), "X-Request-ID": "rid-123456"})
    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line.startswith("{")]
    denied = [x for x in lines if x.get("event") == "request.denied"]
    assert len(denied) == 1
    d = denied[0]
    assert d["reason"] == "untrusted_source" and d["status"] == 401
    assert d["request_ref"] == reference("request", "rid-123456") and "request_id" not in d
    assert d["forwarded_identity_present"] is True and d["client_ip"] == "192.0.2.10" and d["logger"] == \
        "factory.security"
    assert "mallory" not in json.dumps(d)


def test_audit_appends_are_witnessed_in_the_logs(tmp_path, capsys):
    client = build(tmp_path)
    capsys.readouterr()
    client.post("/api/v1/placement/decide", json={"sensitivity": "C1"})
    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line.startswith("{")]
    witness = next(x for x in lines if x.get("event") == "audit.appended")
    assert witness["hash"] == audit_lines(tmp_path)[0]["hash"] and witness["seq"] == 1
    assert "actor" not in witness and "client_ip" not in witness  # minimal personal data in logs


def test_client_ip_comes_from_x_forwarded_for_only_behind_a_trusted_proxy(tmp_path):
    xff = {"X-Forwarded-For": "198.51.100.7, 10.0.0.9"}  # client, then a trusted hop
    build(tmp_path, trusted_proxies=("10.0.0.0/24",)).post("/api/v1/placement/decide", json={"sensitivity": "C1"},
                                                            headers=xff)
    build(tmp_path, peer=UNTRUSTED_PEER, trusted_proxies=("10.0.0.0/24",)).post(
        "/api/v1/placement/decide", json={"sensitivity": "C1"}, headers=xff)
    assert [x["client_ip"] for x in audit_lines(tmp_path)] == ["198.51.100.7", "192.0.2.10"]


# ------------------------------------------------------------------------------------ posture, metrics, leaks
def test_posture_in_health_and_snapshot_meta(tmp_path):
    client = proxy(tmp_path)
    health = client.get("/api/v1/health").json()["security"]
    assert {"status", "auth_mode", "authentication", "rbac", "roles", "trusted_proxies", "separation_of_duties",
            "audit", "rate_limit", "body_limit_bytes", "cors", "security_headers", "warnings"} <= set(health)
    assert health["audit"]["chain_ok"] is True and health["audit"]["head_hash"] == GENESIS
    viewer = who("vera", "grp-viewer")
    en = client.get("/api/v1/snapshot", headers=viewer).json()["meta"]["security"]
    fr = client.get("/api/v1/snapshot", params={"lang": "fr"}, headers=viewer).json()["meta"]["security"]
    assert en == fr == health
    text = json.dumps(health)
    assert "10.0.0.0" not in text and "grp-" not in text  # no network layout, no group names


def test_security_metrics(tmp_path):
    client = proxy(tmp_path)
    client.post("/api/v1/registry/uc-02/model-version", json={"version": "2026-11"}, headers=who("m", "grp-maintainer"))
    client.get("/api/v1/audit", headers=who("v", "grp-viewer"))
    proxy(tmp_path, peer=UNTRUSTED_PEER)  # another app: its own counters
    text = client.get("/metrics").text
    assert 'factory_audit_events_total{action="registry.model_version"} 1.0' in text
    assert 'factory_audit_events_total{action="spaces.put"} 0.0' in text
    assert "factory_audit_chain_ok 1.0" in text
    assert 'factory_security_denied_total{reason="forbidden"} 1.0' in text
    assert "m@example.org" not in text


SENTINELS = ["sk-lf-SENTINEL-secret", "pk-lf-SENTINEL-public", "sk-SENTINEL-master"]


def test_no_secret_in_any_response_with_auth_on(tmp_path):
    token = tmp_path / "token"
    token.write_text("glsa_SENTINEL_token")
    client = proxy(tmp_path, demo_mode="auto", langfuse_host="http://127.0.0.1:9", langfuse_public_key=SENTINELS[1],
                   langfuse_secret_key=SENTINELS[0], litellm_url="http://127.0.0.1:9", litellm_master_key=SENTINELS[2],
                   grafana_url="http://127.0.0.1:9", grafana_token_file=str(token), connector_timeout_s=0.2)
    admin = who("root", "grp-admin")
    client.post("/api/v1/registry/uc-02/model-version", json={"version": "2026-11"}, headers=admin)
    client.post("/api/v1/placement/decide", json={"sensitivity": "C1"}, headers=admin)
    responses = [call(client, m, p, b, headers=admin) for m, p, b, _ in ENDPOINTS]
    responses += [client.get("/api/v1/health"), client.get("/metrics"),
                  client.get("/api/v1/snapshot", headers={"X-Forwarded-User": SENTINELS[2]})]
    for r in responses:
        for secret in SENTINELS + ["glsa_SENTINEL_token"]:
            assert secret not in r.text, (r.request.url, secret)
    assert SENTINELS[2] not in (tmp_path / "state" / "audit.jsonl").read_text()


def test_unauthenticated_large_body_is_refused_before_it_is_read(tmp_path):
    client = proxy(tmp_path, max_body_bytes=2048)
    big = json.dumps({"version": 0, "state": {"x": "y" * 5000}})
    r = client.put("/api/v1/spaces/state", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 401
    r = client.put("/api/v1/spaces/state", content=big,
                   headers={"content-type": "application/json", **who("eli", "grp-editor")})
    assert r.status_code == 413  # the body-size limit still applies to authenticated callers
