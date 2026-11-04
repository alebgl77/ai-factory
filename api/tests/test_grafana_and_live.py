"""Connectors with httpx.MockTransport: Grafana proxy safety, live/mixed mode, graceful failure."""

import json

import httpx
import pytest

from app.connectors.grafana import GrafanaClient, PathNotAllowed, is_allowed
from app.connectors.langfuse import samples_from_observations
from app.connectors.base import SourceError

TOKEN = "glsa_TESTTOKEN_0123456789abcdef"
SECRETS = [TOKEN, "sk-lf-SECRETKEY-xyz", "pk-lf-PUBLICKEY-xyz", "sk-master-SECRET-123"]


@pytest.fixture
def token_file(tmp_path):
    p = tmp_path / "grafana_token"
    p.write_text(TOKEN + "\n")
    return str(p)


class Recorder:
    """Mock upstream services; records every request it receives."""

    def __init__(self, grafana=True, prometheus=False, langfuse=False, litellm=False, qdrant=False,
                 echo_token=False):
        self.calls: list[httpx.Request] = []
        self.flags = dict(grafana=grafana, prometheus=prometheus, langfuse=langfuse, litellm=litellm, qdrant=qdrant)
        self.echo_token = echo_token

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        host, path = request.url.host, request.url.path
        if host == "grafana" and self.flags["grafana"]:
            if request.headers.get("authorization") != f"Bearer {TOKEN}":
                return httpx.Response(401, json={"message": "unauthorized"})
            if path == "/api/search":
                items = [{"uid": "factory-overview", "title": "Vue d'ensemble", "url": "/d/factory-overview/vue",
                          "tags": ["factory"], "folderTitle": "Factory"},
                         {"uid": "bad uid!", "title": "ignored", "url": "/d/x"}]
                if self.echo_token:
                    items[0]["title"] = f"leak {TOKEN}"
                return httpx.Response(200, json=items)
            if path.startswith("/api/dashboards/uid/"):
                return httpx.Response(200, json={"dashboard": {"uid": "factory-overview", "title": "Vue",
                                                               "panels": [{"id": 1, "title": "GPU", "type": "timeseries",
                                                                           "targets": [{"expr": "secret"}]}]},
                                                 "meta": {"url": "/d/factory-overview/vue"}})
            if path == "/api/datasources/proxy/uid/prometheus/api/v1/query":
                return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": [
                    {"metric": {"gpu": "0"}, "value": [1, "42.5"]}]}})
            return httpx.Response(404)
        if host == "prometheus" and self.flags["prometheus"]:
            return self.prometheus(request)
        if host == "langfuse" and self.flags["langfuse"]:
            if path == "/api/public/health":
                return httpx.Response(200, json={"status": "OK"})
            if path == "/api/public/observations":
                return httpx.Response(200, json={"data": self.observations(request.url.params.get("type")),
                                                 "meta": {"totalPages": 1}})
        if host == "litellm" and self.flags["litellm"]:
            if path == "/health/liveliness":
                return httpx.Response(200, json="I'm alive!")
            if path == "/model/info":
                return httpx.Response(200, json={"data": [{"model_name": "chat-default", "litellm_params": {
                    "model": "openai/mock", "api_key": "sk-master-SECRET-123", "api_base": "http://mock"},
                    "model_info": {"mode": "chat"}}]})
        if host == "qdrant" and self.flags["qdrant"]:
            if path in ("/readyz", "/healthz"):
                return httpx.Response(200, text="ok")
            if path == "/collections":
                return httpx.Response(200, json={"result": {"collections": [{"name": "kb-docs"}, {"name": "kb-ghost"}]}})
            if path.startswith("/collections/"):
                return httpx.Response(200, json={"result": {"points_count": 1000, "status": "green"}})
        return httpx.Response(503)

    @staticmethod
    def prometheus(request):
        path = request.url.path
        q = request.url.params.get("query", "")
        if path == "/-/ready":
            return httpx.Response(200, text="ready")
        if path == "/api/v1/query" and "factory_llm_requests_total" in q:
            status_filter = 'status!="success"' in q
            rows = {"cloud": ("120", "2"), "vps": ("40", "3"), "local": ("10", "0")}
            return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": [
                {"metric": {"deployment": d}, "value": [1, v[1] if status_filter else v[0]]} for d, v in rows.items()]}})
        if path == "/api/v1/query" and "factory_llm_latency_seconds_bucket" in q:
            return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": [
                {"metric": {"deployment": "cloud"}, "value": [1, "0.9"]}]}})
        if path == "/api/v1/query":
            values = {"DCGM_FI_DEV_GPU_UTIL": "63", "DCGM_FI_DEV_GPU_TEMP": "61", "DCGM_FI_DEV_POWER_USAGE": "180",
                      "DCGM_FI_DEV_FB_USED": "10240", "DCGM_FI_DEV_FB_FREE": "14336"}
            for k, v in values.items():
                if k in q:
                    return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": [
                        {"metric": {"gpu": "0", "modelName": "GPU de test"}, "value": [1, v]}]}})
            return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": []}})
        if path == "/api/v1/query_range":
            start, end, step = (float(request.url.params[k]) for k in ("start", "end", "step"))
            ts = [start + i * step for i in range(int((end - start) // step) + 1)]
            if "factory_llm_cost_eur_total" in q:
                result = [{"metric": {"usecase": "uc-01", "model": "chat-default"}, "values": [[t, "3.5"] for t in ts]}]
            elif "factory_llm_tokens_total" in q:
                result = [{"metric": {"usecase": "uc-01", "direction": "in"}, "values": [[t, "10000"] for t in ts]},
                          {"metric": {"usecase": "uc-01", "direction": "out"}, "values": [[t, "2000"] for t in ts]}]
            elif "factory_llm_requests_total" in q:
                result = [{"metric": {"usecase": "uc-01"}, "values": [[t, "40"] for t in ts]}]
            elif "DCGM" in q:
                result = [{"metric": {"gpu": "0"}, "values": [[t, "5" if i < 6 else "60"] for i, t in enumerate(ts)]}]
            else:
                result = []
            return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": result}})
        return httpx.Response(404)

    @staticmethod
    def observations(obs_type):
        if obs_type == "GENERATION":
            return [{"id": f"g{i}", "traceId": f"t{i}", "type": "GENERATION", "metadata": {"agent": "ag-research"},
                     "startTime": "2026-11-03T08:30:00.000Z", "completionStartTime": f"2026-11-03T08:30:00.{400 + i}Z",
                     "usageDetails": {"input": 3000, "output": 500, "total": 3500}} for i in range(5)]
        return [{"id": f"s{i}", "traceId": f"t{i}", "type": "SPAN", "name": "qdrant-retrieval",
                 "startTime": "2026-11-03T08:29:59.900Z", "endTime": "2026-11-03T08:29:59.960Z"} for i in range(5)]


def live_settings(token_file, **flags):
    s = dict(demo_mode="auto", grafana_url="http://grafana:3000", grafana_token_file=token_file)
    if flags.get("prometheus"):
        s["prometheus_url"] = "http://prometheus:9090"
    if flags.get("langfuse"):
        s.update(langfuse_host="http://langfuse:3000", langfuse_public_key="pk-lf-PUBLICKEY-xyz",
                 langfuse_secret_key="sk-lf-SECRETKEY-xyz")
    if flags.get("litellm"):
        s.update(litellm_url="http://litellm:4000", litellm_master_key="sk-master-SECRET-123")
    if flags.get("qdrant"):
        s["qdrant_url"] = "http://qdrant:6333"
    return s


def assert_no_secret(text):
    for secret in SECRETS:
        assert secret not in text


# ---------------------------------------------------------------- Grafana proxy
@pytest.mark.parametrize("path,ok", [
    ("/api/search", True), ("/api/dashboards/uid/factory-overview", True),
    ("/api/datasources/proxy/uid/prometheus/api/v1/query", True),
    ("/api/admin/users", False), ("/api/datasources", False), ("/api/serviceaccounts", False),
    ("/api/datasources/proxy/uid/prometheus/api/v1/admin/tsdb/delete_series", False),
    ("/api/dashboards/uid/../../admin", False), ("/api/search/../admin", False),
    ("/api/datasources/proxy/uid/prometheus/api/v1/query_range", False),
])
def test_allow_list(path, ok):
    assert is_allowed(path) is ok


def test_non_allow_listed_path_refused_without_http_call(token_file):
    rec = Recorder()
    client = GrafanaClient("http://grafana:3000", token_file, "prometheus", transport=httpx.MockTransport(rec))
    with pytest.raises(PathNotAllowed):
        client.get("/api/admin/users")
    assert rec.calls == []


def test_grafana_client_only_issues_get(token_file):
    rec = Recorder()
    client = GrafanaClient("http://grafana:3000", token_file, "prometheus", transport=httpx.MockTransport(rec))
    client.dashboards()
    client.dashboard("factory-overview")
    client.metric("gpu_util")
    assert rec.calls and all(c.method == "GET" for c in rec.calls)
    assert all(c.headers["authorization"] == f"Bearer {TOKEN}" for c in rec.calls)
    with pytest.raises(KeyError):
        client.metric("up; drop")


def test_dashboards_through_api_token_never_returned(make_client, token_file):
    rec = Recorder(echo_token=True)
    c = make_client(transport=httpx.MockTransport(rec), **live_settings(token_file))
    r = c.get("/api/v1/grafana/dashboards")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["dashboards"][0]["uid"] == "factory-overview"
    assert body["dashboards"][0]["url"] == "/grafana/d/factory-overview/vue"
    assert len(body["dashboards"]) == 1  # invalid uid dropped
    assert "[redacted]" in body["dashboards"][0]["title"]
    assert_no_secret(r.text)
    assert any(q["name"] == "gpu_util" for q in body["queries"])
    snap = c.get("/api/v1/snapshot")
    assert snap.json()["grafana"]["available"] is True
    assert_no_secret(snap.text)


def test_named_metric_through_datasource_proxy(make_client, token_file):
    rec = Recorder()
    c = make_client(transport=httpx.MockTransport(rec), **live_settings(token_file))
    r = c.get("/api/v1/grafana/metric/gpu_util")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == [{"metric": {"gpu": "0"}, "value": 42.5}] and body["source"] == "grafana"
    last = rec.calls[-1]
    assert last.url.path == "/api/datasources/proxy/uid/prometheus/api/v1/query"
    assert last.url.params["query"] == "avg by (gpu) (DCGM_FI_DEV_GPU_UTIL)"
    assert_no_secret(r.text)


def test_unknown_metric_and_injection_refused(make_client, token_file):
    rec = Recorder()
    c = make_client(transport=httpx.MockTransport(rec), **live_settings(token_file))
    n = len(rec.calls)
    assert c.get("/api/v1/grafana/metric/not_a_query").status_code == 404
    assert c.get("/api/v1/grafana/metric/up%7Bjob%3D%22x%22%7D").status_code in (404, 422)
    assert c.get("/api/v1/grafana/metric/gpu_util?query=up").json()["query"] == "avg by (gpu) (DCGM_FI_DEV_GPU_UTIL)"
    assert c.post("/api/v1/grafana/metric/gpu_util").status_code == 405
    assert c.get("/api/v1/grafana/api/admin/users").status_code == 404
    grafana_paths = [x.url.path for x in rec.calls[n:] if x.url.host == "grafana"]
    assert all(is_allowed(p) for p in grafana_paths)


def test_grafana_unavailable(make_client, token_file):
    c = make_client(transport=httpx.MockTransport(Recorder(grafana=False)), **live_settings(token_file))
    assert c.get("/api/v1/grafana/dashboards").json()["available"] is False
    assert c.get("/api/v1/grafana/metric/gpu_util").status_code == 502
    c = make_client()  # demo mode always
    assert c.get("/api/v1/grafana/metric/gpu_util").status_code == 503


def test_missing_or_bad_token_file(tmp_path):
    rec = Recorder()
    client = GrafanaClient("http://grafana:3000", str(tmp_path / "missing"), "prometheus",
                           transport=httpx.MockTransport(rec))
    assert client.health() is False
    with pytest.raises(SourceError):
        client.dashboards()
    bad = tmp_path / "bad"
    bad.write_text("two words\n")
    assert GrafanaClient("http://grafana:3000", str(bad), "prometheus", transport=httpx.MockTransport(rec))._token() is None


# ---------------------------------------------------------------- live / mixed mode
def test_mixed_mode_with_live_sources(make_client, token_file):
    flags = dict(prometheus=True, langfuse=True, litellm=True, qdrant=True)
    rec = Recorder(**flags)
    c = make_client(transport=httpx.MockTransport(rec), **live_settings(token_file, **flags))
    health = c.get("/api/v1/health").json()
    assert health["sources"] == {"prometheus": True, "grafana": True, "langfuse": True, "litellm": True, "qdrant": True}
    r = c.get("/api/v1/snapshot")
    s = r.json()
    assert s["meta"]["mode"] == "mixed"
    assert s["meta"]["sections"]["costs"] == "live" and s["meta"]["sections"]["pipeline"] == "demo"
    assert s["gpu"]["source"] == "dcgm"
    dev = s["gpu"]["devices"][0]
    assert dev["util_pct"] == 63 and dev["mem_total_gb"] == 24.0 and dev["name"] == "GPU de test"
    assert any(a["rule"] == "idle_gpu" for a in s["costs"]["alerts"])  # from live DCGM series
    uc01 = next(u for u in s["costs"]["by_usecase"] if u["usecase"] == "uc-01")
    assert uc01["llm_eur"] == pytest.approx(3.5 * 30, abs=0.01) and uc01["tokens_in"] == 300000
    assert s["agentops"]["by_agent"][0]["agent"] == "ag-research"
    assert s["agentops"]["by_agent"][0]["ttft_ms_p95"] in range(400, 410)
    assert s["agentops"]["global"]["vector_latency_ms_p95"] == 60
    assert s["catalog"]["gateway_models"] == [{"alias": "chat-default", "provider": "openai", "mode": "chat",
                                               "deployment": None, "max_tokens": None}]
    targets = {t["id"]: t for t in s["placement"]["targets"]}
    assert targets["cloud"]["status"] == "up" and targets["cloud"]["latency_ms_p95"] == 900
    assert targets["vps"]["status"] == "degraded"  # 3 errors / 40 requests on the VPS deployment
    assert s["meta"]["sections"]["placement"] == "mixed"
    assert any(a["rule"] == "orphan_index" and a.get("index") == "kb-ghost" for a in s["costs"]["alerts"])
    assert_no_secret(r.text)
    assert_no_secret(json.dumps(health))
    # langfuse calls use basic auth, never the master key
    lf = [x for x in rec.calls if x.url.host == "langfuse" and x.url.path == "/api/public/observations"]
    assert lf and all(x.headers["authorization"].startswith("Basic ") for x in lf)


def test_gpu_sim_label_reported_as_sim(make_client, token_file):
    class SimRecorder(Recorder):
        @staticmethod
        def prometheus(request):
            resp = Recorder.prometheus(request)
            if request.url.path != "/api/v1/query":
                return resp
            data = json.loads(resp.content)
            if True:
                for item in data["data"]["result"]:
                    if "gpu" in item["metric"]:
                        item["metric"]["gpu_source"] = "sim"
            return httpx.Response(resp.status_code, json=data)
    rec = SimRecorder(prometheus=True)
    c = make_client(transport=httpx.MockTransport(rec), **live_settings(token_file, prometheus=True))
    s = c.get("/api/v1/snapshot").json()
    assert s["gpu"]["source"] == "sim" and s["meta"]["sections"]["gpu"] == "live"
    assert s["gpu"]["devices"][0]["telemetry"] == "sim"


def test_litellm_spend_logs_fallback(make_client, token_file):
    class SpendRecorder(Recorder):
        def __call__(self, request):
            if request.url.host == "litellm" and request.url.path == "/spend/logs":
                assert request.headers["authorization"] == "Bearer sk-master-SECRET-123"
                return httpx.Response(200, json=[
                    {"startTime": "2026-11-02T10:00:00Z", "spend": 1.25, "prompt_tokens": 1000,
                     "completion_tokens": 100, "metadata": {"usecase": "uc-02"}, "api_key": "hashed"}])
            return super().__call__(request)
    rec = SpendRecorder(litellm=True)
    c = make_client(transport=httpx.MockTransport(rec), **live_settings(token_file, litellm=True))
    r = c.get("/api/v1/snapshot")
    s = r.json()
    assert s["meta"]["sections"]["costs"] == "live"
    uc02 = next(u for u in s["costs"]["by_usecase"] if u["usecase"] == "uc-02")
    assert uc02["llm_eur"] == 1.25 and uc02["requests"] == 1
    assert any("LiteLLM" in w for w in s["meta"]["warnings"])
    assert_no_secret(r.text)


def test_never_mode_has_no_demo_data(make_client, token_file):
    rec = Recorder(prometheus=True)
    c = make_client(transport=httpx.MockTransport(rec), **{**live_settings(token_file, prometheus=True),
                                                           "demo_mode": "never"})
    s = c.get("/api/v1/snapshot").json()
    assert s["meta"]["mode"] == "live"
    assert s["pipeline"]["items"] == [] and s["dataloop"]["events"] == []
    assert s["gpu"]["source"] == "dcgm"


def test_unreachable_sources_never_crash(make_client, tmp_path):
    def boom(request):
        raise httpx.ConnectError("unreachable", request=request)
    settings = dict(demo_mode="auto", prometheus_url="http://prometheus:9090", grafana_url="http://grafana:3000",
                    grafana_token_file=str(tmp_path / "none"), langfuse_host="http://langfuse:3000",
                    langfuse_public_key="pk", langfuse_secret_key="sk", litellm_url="http://litellm:4000",
                    qdrant_url="http://qdrant:6333")
    c = make_client(transport=httpx.MockTransport(boom), **settings)
    s = c.get("/api/v1/snapshot")
    assert s.status_code == 200
    body = s.json()
    assert body["meta"]["mode"] == "demo" and not any(body["meta"]["sources"].values())
    assert c.get("/api/v1/health").json()["status"] == "ok"


def test_malformed_payloads_fail_soft(make_client, token_file):
    def junk(request):
        if request.url.path in ("/-/ready",):
            return httpx.Response(200, text="ok")
        return httpx.Response(200, text="not json")
    c = make_client(transport=httpx.MockTransport(junk), demo_mode="auto", prometheus_url="http://prometheus:9090")
    body = c.get("/api/v1/snapshot").json()
    assert body["meta"]["sources"]["prometheus"] is True
    assert body["gpu"]["source"] == "sim" and body["meta"]["mode"] == "demo"


def test_langfuse_ttft_from_completion_start():
    obs = [{"traceId": "t1", "type": "GENERATION", "metadata": {"agent": "a"}, "startTime": "2026-11-03T08:00:00Z",
            "completionStartTime": "2026-11-03T08:00:01.250Z", "usage": {"input": 10, "output": 5}},
           {"traceId": "t1", "type": "GENERATION", "metadata": {}, "startTime": "2026-11-03T08:00:05Z",
            "completionStartTime": "2026-11-03T08:00:05.100Z", "usageDetails": {"total": 20}, "level": "ERROR"}]
    out = samples_from_observations(obs)
    assert out == {"a": [{"ttft_ms": 1250.0, "tokens": 35, "vector_latency_ms": None, "success": False, "eur": 0.0}]}
