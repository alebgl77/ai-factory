import json

import pytest

from tests.conftest import review_body

SNAPSHOT_KEYS = {"meta", "factory", "profiles", "registry", "costs", "pipeline", "dataloop", "placement",
                 "agentops", "catalog", "skills", "rnd", "gpu", "grafana"}


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["mode"] == "demo"
    assert set(body["sources"]) == {"prometheus", "grafana", "langfuse", "litellm", "qdrant"}
    assert not any(body["sources"].values())


def test_snapshot_schema(client):
    r = client.get("/api/v1/snapshot")
    assert r.status_code == 200
    s = r.json()
    assert set(s) >= SNAPSHOT_KEYS
    meta = s["meta"]
    assert meta["mode"] == "demo" and meta["seed"] == 42 and meta["fine_tuning"] is False
    assert {"generated_at", "sources", "version"} <= set(meta)
    assert {"name", "tagline", "currency", "regulatory"} <= set(s["factory"])
    assert {"roles", "people"} <= set(s["profiles"])
    item = next(i for i in s["registry"] if i["id"] == "uc-01")
    assert {"id", "code", "name", "stage", "sensitivity", "sensitive", "color", "owner", "cost_center", "task_type",
            "risk", "model", "squad", "kpis"} <= set(item)
    assert {"tier", "gpai", "annex_iii_area", "applicable_from", "days_left", "reevaluation_required", "reason",
            "required_controls", "nist_progress", "nist", "iso42001"} <= set(item["risk"])
    assert item["risk"]["reevaluation_required"] is True
    assert item["risk"]["reason"] == "model 2026-09 not reviewed (last 2026-06)"
    c = s["costs"]
    assert {"days", "mode", "totals", "by_usecase", "by_cost_center", "alerts"} <= set(c)
    assert {"tokens_in", "tokens_out", "llm_eur", "gpu_eur", "platform_eur", "total_eur"} <= set(c["totals"])
    assert {"usecase", "tokens_in", "tokens_out", "cached_ratio", "requests", "retry_ratio", "llm_eur", "gpu_hours",
            "gpu_eur", "platform_eur", "total_eur", "eur_per_request", "trend_pct", "daily"} <= set(c["by_usecase"][0])
    assert {"cost_center", "name", "budget_eur", "spent_eur", "forecast_eur", "invoice_lines"} <= set(c["by_cost_center"][0])
    assert {"id", "rule", "usecase", "severity", "message", "saving_eur_month", "action"} <= set(c["alerts"][0])
    p = s["pipeline"]
    assert p["stages"] == ["ingestion", "indexing", "versioning", "eval_gate", "deploy", "feedback", "rollback_guard"]
    assert {"usecase", "version", "prompt_version", "agent_version", "stages", "blocked_by"} <= set(p["items"][0])
    assert {"key", "status", "at", "detail"} <= set(p["items"][0]["stages"][0])
    assert {"at", "usecase", "from", "to", "trigger", "value", "threshold", "auto"} <= set(p["rollbacks"][0])
    d = s["dataloop"]
    assert d["retraining"] == "disabled"
    assert {"at", "usecase", "signal", "value", "threshold", "action", "status", "detail"} <= set(d["events"][0])
    pl = s["placement"]
    assert {"id", "label", "status", "util_pct", "eur_per_1k_tokens", "latency_ms_p95"} <= set(pl["targets"][0])
    assert {"id", "usecase", "sensitivity", "target", "fallback", "reasons", "model_alias"} <= set(pl["workloads"][0])
    ao = s["agentops"]
    assert {"window", "slo", "global", "by_agent", "series"} <= set(ao)
    assert {"agent", "usecase", "ttft_ms_p95", "tokens_per_task_p95", "vector_latency_ms_p95", "success_rate", "tasks",
            "eur_per_task", "breaches"} <= set(ao["by_agent"][0])
    assert {"models", "agents", "tools", "repos"} <= set(s["catalog"])
    sk = s["skills"]
    assert {"modules", "roles", "people", "coverage", "sessions", "art4"} <= set(sk)
    assert {"status", "note"} <= set(sk["art4"])
    assert {"experiments", "gpu_hours_month"} <= set(s["rnd"])
    assert s["gpu"]["source"] in ("dcgm", "sim")
    assert {"id", "name", "util_pct", "temp_c", "power_w", "mem_used_gb", "mem_total_gb",
            "sensitive_sessions"} <= set(s["gpu"]["devices"][0])
    assert s["grafana"] == {"available": False, "dashboards": []}
    # sensitive use cases are placed locally
    for w in pl["workloads"]:
        if w["sensitivity"] in ("C3", "C4"):
            assert w["target"] == "local"


@pytest.mark.parametrize("path", ["/api/v1/registry", "/api/v1/registry/uc-01", "/api/v1/costs", "/api/v1/pipeline",
                                  "/api/v1/dataloop", "/api/v1/placement", "/api/v1/agentops", "/api/v1/catalog",
                                  "/api/v1/skills", "/api/v1/gpu", "/api/v1/grafana/dashboards",
                                  "/api/v1/spaces/state", "/api/v1/openapi.json", "/api/v1/docs"])
def test_read_endpoints(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text


def test_registry_item_and_404(client):
    r = client.get("/api/v1/registry/uc-04")
    assert r.json()["risk"]["tier"] == "high" and r.json()["pipeline"]["usecase"] == "uc-04"
    assert client.get("/api/v1/registry/uc-99").status_code == 404
    assert client.get("/api/v1/registry/..%2Fetc").status_code in (404, 422)


def test_post_model_version_flips_reevaluation(client):
    before = client.get("/api/v1/registry/uc-02").json()
    assert before["risk"]["reevaluation_required"] is False
    assert before["pipeline"]["blocked_by"] is None
    r = client.post("/api/v1/registry/uc-02/model-version", json={"version": "2026-11", "name": "cloud/large-v2"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reevaluation_required"] is True
    assert body["deploy_gate"] == "blocked"
    assert "2026-11" in body["reason"]
    assert body["model"]["version"] == "2026-11" and body["model"]["name"] == "cloud/large-v2"
    assert body["model"]["history"][-1]["version"] == "2026-11"
    after = client.get("/api/v1/registry/uc-02").json()
    assert after["risk"]["reevaluation_required"] is True
    assert after["pipeline"]["blocked_by"] == "risk_reassessment"
    deploy = next(st for st in after["pipeline"]["stages"] if st["key"] == "deploy")
    assert deploy["status"] == "blocked"
    snap = client.get("/api/v1/snapshot").json()
    assert next(i for i in snap["registry"] if i["id"] == "uc-02")["risk"]["reevaluation_required"] is True
    # same version again -> conflict
    assert client.post("/api/v1/registry/uc-02/model-version", json={"version": "2026-11"}).status_code == 409


def test_post_assessment_clears_flag(client):
    assert client.get("/api/v1/registry/uc-01").json()["risk"]["reevaluation_required"] is True
    body = review_body(client, "uc-01")
    r = client.post("/api/v1/registry/uc-01/assessment", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["reevaluation_required"] is False
    item = client.get("/api/v1/registry/uc-01").json()
    assert item["assessment"]["reviewed_model_version"] == "2026-09"
    assert item["assessment"]["reviewed_revision"] == body["expected_review_revision"]
    assert item["pipeline"]["blocked_by"] != "risk_reassessment"
    r = client.post("/api/v1/registry/uc-01/assessment", json={**body, "decision": "pending"})
    assert r.json()["reevaluation_required"] is True


def test_model_version_then_assessment_unblocks(client):
    client.post("/api/v1/registry/uc-02/model-version", json={"version": "2026-12"})
    client.post("/api/v1/registry/uc-02/assessment",
                json=review_body(client, "uc-02", decision="approved_with_conditions"))
    item = client.get("/api/v1/registry/uc-02").json()
    assert item["risk"]["reevaluation_required"] is False
    assert item["pipeline"]["blocked_by"] is None


@pytest.mark.parametrize("payload", [{"version": ""}, {"version": "../x"}, {"version": "a" * 65}, {},
                                     {"version": "1.0", "extra": 1}])
def test_model_version_validation(client, payload):
    assert client.post("/api/v1/registry/uc-01/model-version", json=payload).status_code == 422


def test_assessment_validation(client):
    body = review_body(client, "uc-01")
    assert client.post("/api/v1/registry/uc-01/assessment", json={**body, "decision": "ok"}).status_code == 422
    assert client.post("/api/v1/registry/uc-01/assessment", json={**body, "reviewer": "p99"}).status_code == 422
    assert client.post("/api/v1/registry/uc-01/assessment",
                       json={**body, "expected_review_revision": "x" * 64}).status_code == 422
    assert client.post("/api/v1/registry/uc-01/assessment",
                       json={"decision": "approved", "reviewer": "p07"}).status_code == 422
    assert client.post("/api/v1/registry/uc-99/assessment", json=body).status_code == 404


def test_state_persists_across_app_instances(make_client):
    c1 = make_client()
    c1.post("/api/v1/registry/uc-03/model-version", json={"version": "2026-11"})
    c2 = make_client()
    assert c2.get("/api/v1/registry/uc-03").json()["model"]["version"] == "2026-11"


def test_body_size_limit(make_client):
    c = make_client(max_body_bytes=2048)
    big = {"version": 0, "state": {"blob": "x" * 5000}}
    r = c.put("/api/v1/spaces/state", content=json.dumps(big), headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_body_size_limit_chunked(make_client):
    c = make_client(max_body_bytes=2048)

    def chunks():
        yield b'{"version": 0, "state": {"blob": "'
        for _ in range(10):
            yield b"x" * 500
        yield b'"}}'
    r = c.put("/api/v1/spaces/state", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413


SENTINELS = ["sk-lf-SENTINEL-secret", "pk-lf-SENTINEL-public", "sk-SENTINEL-master"]


def test_no_secret_in_any_get_response(make_client, tmp_path):
    token = tmp_path / "token"
    token.write_text("glsa_SENTINEL_token")
    c = make_client(demo_mode="auto", langfuse_host="http://127.0.0.1:9", langfuse_public_key=SENTINELS[1],
                    langfuse_secret_key=SENTINELS[0], litellm_url="http://127.0.0.1:9", litellm_master_key=SENTINELS[2],
                    grafana_url="http://127.0.0.1:9", grafana_token_file=str(token), connector_timeout_s=0.2)
    schema = c.get("/api/v1/openapi.json").json()
    paths = [p for p, ops in schema["paths"].items() if "get" in ops and "{" not in p]
    paths += ["/api/v1/registry/uc-01", "/api/v1/grafana/metric/gpu_util", "/metrics", "/api/v1/openapi.json"]
    assert "/api/v1/snapshot" in paths and "/metrics" in paths
    for path in paths:
        r = c.get(path)
        for secret in SENTINELS + ["glsa_SENTINEL_token"]:
            assert secret not in r.text, (path, secret)


def test_spaces_optimistic_versioning(client):
    assert client.get("/api/v1/spaces/state").json()["version"] == 0
    r = client.put("/api/v1/spaces/state", json={"version": 0, "state": {"bookings": [{"resource": "S1"}]}})
    assert r.status_code == 200 and r.json()["version"] == 1
    stale = client.put("/api/v1/spaces/state", json={"version": 0, "state": {}})
    assert stale.status_code == 409 and stale.json()["current"]["version"] == 1
    assert client.get("/api/v1/spaces/state").json()["state"] == {"bookings": [{"resource": "S1"}]}
    deep: dict = {}
    cur = deep
    for _ in range(12):
        cur["a"] = {}
        cur = cur["a"]
    assert client.put("/api/v1/spaces/state", json={"version": 1, "state": deep}).status_code == 422


def test_placement_decide(client):
    r = client.post("/api/v1/placement/decide", json={"usecase": "uc-04", "targets": {"local": "down"}})
    assert r.status_code == 200
    d = r.json()["decision"]
    assert d["status"] == "queued" and d["target"] not in ("cloud", "vps")
    r = client.post("/api/v1/placement/decide", json={"sensitivity": "C1", "task_type": "classification",
                                                      "targets": {"cloud": "down"}})
    assert r.json()["decision"]["target"] == "vps"
    assert client.post("/api/v1/placement/decide", json={}).status_code == 422
    assert client.post("/api/v1/placement/decide", json={"sensitivity": "C9"}).status_code == 422


def test_costs_params(client):
    c = client.get("/api/v1/costs?days=7&mode=chargeback").json()
    assert c["days"] == 7 and c["mode"] == "chargeback"
    assert len(c["by_usecase"][0]["daily"]) == 7
    assert round(sum(u["total_eur"] for u in c["by_usecase"]), 2) == c["totals"]["total_eur"]
    assert client.get("/api/v1/costs?days=0").status_code == 422
    assert client.get("/api/v1/costs?mode=invoice").status_code == 422


def test_metrics_exposition(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    text = r.text
    for name in ("factory_usecase_cost_eur", "factory_usecase_tokens_total", "factory_waste_alerts",
                 "factory_risk_usecases", "factory_reevaluation_required", "factory_pipeline_gate_pass",
                 "factory_rollbacks_total", "factory_skills_coverage_ratio", "factory_agent_ttft_seconds",
                 "factory_agent_tokens_per_task", "factory_vector_latency_seconds"):
        assert f"\n{name}{{" in text or text.startswith(f"{name}{{"), name
    assert 'factory_usecase_cost_eur{cost_center="cc-ops",kind="llm",usecase="uc-01"}' in text
    assert 'factory_reevaluation_required{usecase="uc-01"} 1.0' in text
    assert 'factory_risk_usecases{tier="prohibited"} 1.0' in text


def test_cors_closed_by_default(client):
    r = client.get("/api/v1/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
    pre = client.options("/api/v1/health", headers={"Origin": "https://evil.example",
                                                    "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in {k.lower() for k in pre.headers}


def test_cors_opt_in(make_client):
    c = make_client(cors_origins=("https://factory.example",))
    r = c.get("/api/v1/health", headers={"Origin": "https://factory.example"})
    assert r.headers.get("access-control-allow-origin") == "https://factory.example"


def test_security_headers(client):
    r = client.get("/api/v1/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"


def test_write_methods_not_allowed_on_read_endpoints(client):
    assert client.post("/api/v1/snapshot").status_code == 405
    assert client.delete("/api/v1/registry/uc-01").status_code == 405


def test_unwritable_state_dir_returns_503(make_client, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    c = make_client(state_dir=str(blocker / "state"))
    assert c.get("/api/v1/snapshot").status_code == 200  # reads still work from the Git baseline
    r = c.post("/api/v1/registry/uc-02/model-version", json={"version": "2026-11"})
    assert r.status_code == 503
    assert c.put("/api/v1/spaces/state", json={"version": 0, "state": {}}).status_code == 503


def test_snapshot_carries_the_framework_crosswalk(client):
    s = client.get("/api/v1/snapshot").json()
    cw = s["frameworks"]["crosswalk"]
    assert cw and all({"id", "nist", "ai_act", "iso42001"} <= set(c) for c in cw)
