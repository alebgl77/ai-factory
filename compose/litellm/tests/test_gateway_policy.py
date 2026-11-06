# SPDX-License-Identifier: MIT

import asyncio
import json
from pathlib import Path
import ssl
import sys
from types import SimpleNamespace

import httpx
import pytest
import yaml


REGISTRY = Path(__file__).resolve().parents[3] / "config" / "usecases"
RAW_KEY = "sk-" + "a" * 36
DIGEST = "a" * 64


def binding(**changes):
    return {"key_sha256": DIGEST, "team_id": "people-workloads", "usecase": "uc-04"} | changes


def write_policy(path, *rows):
    path.write_text(json.dumps({"version": 1, "bindings": rows}), encoding="utf-8")


@pytest.fixture
def strict_settings(fc, tmp_path, monkeypatch):
    path = tmp_path / "policy.json"
    write_policy(path, binding(allow_embeddings=True))
    monkeypatch.setenv("FACTORY_GATEWAY_POLICY", str(path))
    monkeypatch.setenv("FACTORY_GATEWAY_REGISTRY", str(REGISTRY))
    monkeypatch.setenv("FACTORY_DEMO_MODE", "never")
    monkeypatch.setenv("LOCAL_CHAT_MODEL", "openai/local-chat")
    monkeypatch.setenv("LOCAL_EMBED_MODEL", "openai/local-embed")
    monkeypatch.setattr(fc, "GUARDRAIL_MODE", "block")
    monkeypatch.setattr(fc, "PII_REDACTION", True)
    litellm = sys.modules["litellm"]
    monkeypatch.setattr(litellm, "turn_off_message_logging", False, raising=False)
    monkeypatch.setattr(litellm, "aclient_session", None, raising=False)
    monkeypatch.setattr(litellm, "_factory_strict_http_client", None, raising=False)


@pytest.fixture
def strict(fc, strict_settings):
    callback = fc.FactoryMetrics()
    yield callback
    # These unit tests exercise authorization without connecting the transport.
    asyncio.run(callback._strict_http_client.aclose())


def auth(**changes):
    return SimpleNamespace(**({
        "api_key": DIGEST, "via_virtual_key": True, "user_role": "internal_user",
        "team_id": "people-workloads", "request_route": "/v1/chat/completions",
        "allowed_routes": ["/v1/chat/completions", "/v1/embeddings"],
        "models": ["chat-sensitive", "embed-default"],
    } | changes))


def body(**changes):
    return {"model": "chat-sensitive", "messages": [{"role": "user", "content": "Summarize the document."}]} | changes


def invoke(callback, identity, data, call="acompletion"):
    return asyncio.run(callback.async_pre_call_hook(identity, None, data, call))


def deny(fc, callback, identity, data, call="acompletion"):
    with pytest.raises(fc.HTTPException) as failure:
        invoke(callback, identity, data, call)
    assert failure.value.status_code == 403
    assert failure.value.detail["error"]["code"] == "factory_gateway_denied"
    assert RAW_KEY not in str(failure.value.detail) and DIGEST not in str(failure.value.detail)


@pytest.mark.parametrize("supplied_sensitivity", [None, "C1", "C4"])
def test_authenticated_case_overrides_downgrades_in_both_metadata_buckets(strict, supplied_sensitivity):
    result = invoke(strict, auth(), body(metadata={"sensitivity": supplied_sensitivity},
        litellm_metadata={"sensitivity": "C2", "user_api_key_team_id": "other", "langfuse_host": "unsafe"},
        unrelated_provider_option="discarded", disable_fallbacks=False))
    assert result["model"] == "chat-sensitive" and result["disable_fallbacks"] is True
    assert "unrelated_provider_option" not in result
    for channel in ("metadata", "litellm_metadata"):
        metadata = result[channel]
        assert (metadata["usecase"], metadata["factory_business_unit"], metadata["user_api_key_team_id"]) == (
            "uc-04", "cc-rh", "people-workloads")
        assert metadata["sensitivity"] == ("C4" if supplied_sensitivity == "C4" else "C3")
        assert metadata["mask_input"] and metadata["mask_output"] and metadata["turn_off_message_logging"]
        assert "langfuse_host" not in metadata and DIGEST not in str(metadata)
    assert sys.modules["litellm"].turn_off_message_logging is True


@pytest.mark.parametrize("identity", [
    {"via_virtual_key": False}, {"user_role": "proxy_admin"}, {"team_id": "finance-workloads"},
    {"allowed_routes": ["llm_api_routes"]}, {"allowed_routes": ["/v1/chat/completions", "/key/update"]},
    {"allowed_routes": []}, {"request_route": "/v1/responses"}, {"api_key": ""}, {"api_key": "b" * 64},
    {"api_key": RAW_KEY}, {"api_key": RAW_KEY, "token": DIGEST},
    {"models": ["chat-default", "chat-sensitive", "embed-default"]}, {"aliases": {"chat-sensitive": "chat-default"}},
    {"router_settings": {"fallbacks": ["chat-default"]}},
])
def test_requires_authenticated_narrow_virtual_key(fc, strict, identity):
    deny(fc, strict, auth(**identity), body())


def test_request_fingerprint_cannot_replace_authenticated_value(fc, strict):
    spoofed = body(metadata={"user_api_key": DIGEST, "user_api_key_hash": DIGEST},
                   litellm_metadata={"user_api_key_hash": DIGEST})
    deny(fc, strict, auth(api_key="b" * 64), spoofed)


@pytest.mark.parametrize("override", [
    {"model": "chat-default", "metadata": {"usecase": "uc-04", "sensitivity": "C1"}},
    {"metadata": {"usecase": "uc-01"}}, {"litellm_metadata": {"usecase": "uc-01"}},
    {"metadata": {"sensitivity": "unclassified"}}, {"api_base": "https://unapproved.invalid"},
    {"fallbacks": [{"chat-sensitive": ["chat-default"]}]}, {"router_settings_override": {"fallbacks": []}},
    {"extra_body": {"api_base": "https://unapproved.invalid"}},
    {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": "https://unapproved.invalid"}]}]},
    {"tools": [{"type": "web_search"}]},
])
def test_refuses_provider_case_and_remote_content_overrides(fc, strict, override):
    deny(fc, strict, auth(), body(**override))


def test_embeddings_require_explicit_local_model_and_route(fc, strict):
    identity = auth(request_route="/v1/embeddings")
    result = invoke(strict, identity, {"model": "embed-default", "input": ["text"]}, "aembedding")
    assert result["model"] == "embed-default" and result["metadata"]["sensitivity"] == "C3"
    assert result["disable_fallbacks"] and result["metadata"]["mask_input"]
    deny(fc, strict, identity, {"model": "embed-default", "input": ["text"]}, "aresponses")


@pytest.mark.parametrize("call,identity,data", [
    ("completion", {}, body()),
    ("embedding", {"request_route": "/v1/embeddings"}, {"model": "embed-default", "input": ["text"]}),
])
def test_synchronous_inference_is_outside_strict_transport(fc, strict, call, identity, data):
    deny(fc, strict, auth(**identity), data, call)


@pytest.mark.parametrize("code", [307, 308])
def test_owned_transport_never_resends_sensitive_post_to_redirect(fc, strict_settings, monkeypatch, code):
    requested = []

    def gateway(request):
        requested.append((str(request.url), request.method))
        return httpx.Response(code, headers={"Location": "https://remote.invalid/capture"})

    constructor = httpx.AsyncClient

    def mock_client(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        assert isinstance(kwargs["verify"], ssl.SSLContext) and kwargs["verify"].check_hostname
        assert kwargs["verify"].verify_mode == ssl.CERT_REQUIRED
        return constructor(transport=httpx.MockTransport(gateway), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)
    callback = fc.FactoryMetrics()
    assert sys.modules["litellm"].aclient_session is callback._strict_http_client

    async def call_local():
        async with callback._strict_http_client as client:
            return await client.post("https://gpu.internal/v1/chat/completions", json=body())

    assert asyncio.run(call_local()).status_code == code
    assert requested == [("https://gpu.internal/v1/chat/completions", "POST")]


def test_only_one_owned_transport_and_runtime_replacement_fails(fc, strict, monkeypatch):
    assert fc.FactoryMetrics()._strict_http_client is strict._strict_http_client
    monkeypatch.setattr(sys.modules["litellm"], "aclient_session", object())
    with pytest.raises(fc.HTTPException) as failure:
        invoke(strict, auth(), body())
    assert failure.value.status_code == 503


def test_strict_startup_rejects_foreign_transport(fc, strict_settings, monkeypatch):
    monkeypatch.setattr(sys.modules["litellm"], "aclient_session", object())
    with pytest.raises(ValueError, match="preconfigured HTTP client"):
        fc.FactoryMetrics()


@pytest.mark.parametrize("name", ["LOCAL_CHAT_MODEL", "LOCAL_EMBED_MODEL"])
def test_strict_startup_rejects_non_openai_local_provider(fc, strict_settings, monkeypatch, name):
    monkeypatch.setenv(name, "anthropic/unapproved-provider")
    with pytest.raises(ValueError, match="OpenAI-compatible local models"):
        fc.FactoryMetrics()


def test_shipped_local_aliases_have_only_configured_openai_transports_and_no_fallback():
    source = Path(__file__).resolve().parents[1] / "config.yaml"
    config = yaml.safe_load(source.read_text(encoding="utf-8"))
    for alias, prefix, base in (("chat-sensitive", "LOCAL_CHAT_MODEL", "LOCAL_API_BASE"),
                                ("embed-default", "LOCAL_EMBED_MODEL", "LOCAL_EMBED_API_BASE")):
        entries = [entry for entry in config["model_list"] if entry["model_name"] == alias]
        assert len(entries) == 1
        params = entries[0]["litellm_params"]
        assert params["model"] == f"os.environ/{prefix}" and params["api_base"] == f"os.environ/{base}"
        assert "custom_llm_provider" not in params and "fallbacks" not in params
        for field in ("fallbacks", "context_window_fallbacks", "content_policy_fallbacks"):
            assert all(alias not in entry for entry in config["router_settings"].get(field, []))
        assert not config["router_settings"].get("default_fallbacks")


def test_cloud_bound_case_cannot_send_an_explicitly_sensitive_request(fc, strict, tmp_path):
    path = tmp_path / "policy.json"
    write_policy(path, binding(usecase="uc-01"))
    strict.gateway_policy = fc._load_gateway_policy(str(path), str(REGISTRY))
    identity = auth(models=["chat-default"], allowed_routes=["/v1/chat/completions"])
    deny(fc, strict, identity, body(model="chat-default", metadata={"sensitivity": "C3"}))
    accepted = invoke(strict, identity, body(model="chat-default", metadata={"sensitivity": "C1"}))
    assert accepted["model"] == "chat-default" and accepted["metadata"]["sensitivity"] == "C2"


@pytest.mark.parametrize("rows", [[], [binding(usecase="missing")], [binding(), binding()],
    [binding(), binding(key_sha256="b" * 64, usecase="uc-01")], [binding(allow_embeddings="false")],
    [binding(key_sha256=RAW_KEY)]])
def test_invalid_or_ambiguous_policy_fails_without_exposing_binding(fc, tmp_path, rows):
    path = tmp_path / "policy.json"
    write_policy(path, *rows)
    with pytest.raises(ValueError, match="Invalid or unavailable Factory gateway policy") as failure:
        fc._load_gateway_policy(str(path), str(REGISTRY))
    assert DIGEST not in str(failure.value) and RAW_KEY not in str(failure.value)


def test_duplicate_registry_field_and_unapproved_local_alias_fail(fc, tmp_path):
    policy = tmp_path / "policy.json"
    write_policy(policy, binding())
    registry = tmp_path / "usecases"
    registry.mkdir()
    case = registry / "uc-04.yaml"
    case.write_text("id: uc-04\ncost_center: cc-rh\nsensitivity: C3\nsensitivity: C1\nmodel: {alias: chat-sensitive}\n")
    with pytest.raises(ValueError, match="Invalid or unavailable Factory gateway policy"):
        fc._load_gateway_policy(str(policy), str(registry))
    case.write_text("id: uc-04\ncost_center: cc-rh\nsensitivity: C3\nmodel: {alias: chat-default}\n")
    with pytest.raises(ValueError, match="Invalid or unavailable Factory gateway policy"):
        fc._load_gateway_policy(str(policy), str(registry))


def test_production_refuses_missing_policy(fc, monkeypatch):
    monkeypatch.delenv("FACTORY_GATEWAY_POLICY", raising=False)
    monkeypatch.setenv("FACTORY_DEMO_MODE", "never")
    with pytest.raises(ValueError, match="gateway policy is required"):
        fc.FactoryMetrics()
