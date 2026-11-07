# SPDX-License-Identifier: MIT

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from scripts.check_production import REQUIRED_CREDENTIALS, production_errors


ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "scripts" / "check_production.py"
PRODUCTION_HOST = "factory.company.internal"
FIXTURE_VALUE = "synthetic-credential"


@pytest.fixture
def valid_config():
    services = {
        service: {"environment": dict.fromkeys(keys, FIXTURE_VALUE)}
        for service, keys in REQUIRED_CREDENTIALS.items()
    }
    services.update({name: {"environment": {}} for name in ("edge", "api")})
    for name in ("edge", "api", "oauth2-proxy"):
        services[name]["networks"] = {"edge-identity": None}
    services["edge"]["networks"]["edge-identity"] = {"ipv4_address": "10.89.255.2"}
    services["edge"]["ports"] = [
        {"target": 443, "published": "443", "host_ip": "0.0.0.0", "protocol": "tcp"},
        {"target": 3001, "published": "3001", "host_ip": "127.0.0.1", "protocol": "tcp"},
    ]
    services["edge"]["environment"].update(
        FACTORY_SCHEME="https", FACTORY_HOST=PRODUCTION_HOST, FACTORY_AUTH="sso", FACTORY_PRIVATE_TELEMETRY="disabled",
    )
    services["api"]["environment"].update(
        FACTORY_AUTH_MODE="proxy", FACTORY_DEFAULT_ROLE="", FACTORY_ROLE_MAP="staff:viewer,governance:approver",
        FACTORY_TRUSTED_PROXIES="10.89.255.2/32", FACTORY_DEMO_MODE="never", FACTORY_WORKBENCH_MODE="off",
        FACTORY_BU_POLICY="/config/business-units.yaml",
    )
    services["oauth2-proxy"]["environment"].update(
        OAUTH2_PROXY_OIDC_ISSUER_URL="https://idp.company.internal:8443/realm",
        OAUTH2_PROXY_REDIRECT_URL=f"https://{PRODUCTION_HOST}/oauth2/callback",
        OAUTH2_PROXY_WHITELIST_DOMAINS=PRODUCTION_HOST, OAUTH2_PROXY_CODE_CHALLENGE_METHOD="S256",
        OAUTH2_PROXY_TRUSTED_PROXY_IPS="10.89.255.2/32", OAUTH2_PROXY_EMAIL_DOMAINS="company.internal",
        OAUTH2_PROXY_COOKIE_SECURE="true", OAUTH2_PROXY_COOKIE_HTTPONLY="true",
        OAUTH2_PROXY_COOKIE_SAMESITE="lax", OAUTH2_PROXY_COOKIE_NAME="__Host-factory-sso",
    )
    services["litellm"]["environment"].update(
        FACTORY_GUARDRAIL_MODE="block", FACTORY_ENFORCE_SENSITIVE_ROUTING="true",
        FACTORY_PII_REDACTION="true", FACTORY_SENSITIVE_CLASSES="C3,C4",
        FACTORY_DEMO_MODE="never", FACTORY_GATEWAY_POLICY="/run/factory-gateway/policy.json",
        FACTORY_GATEWAY_REGISTRY="/factory-usecases",
        LOCAL_API_BASE="http://10.40.20.15:8000/v1", LOCAL_EMBED_API_BASE="http://[fd00::20]:8000/v1",
        LOCAL_CHAT_MODEL="openai/local-chat", LOCAL_EMBED_MODEL="openai/local-embed",
    )
    services["api"]["volumes"] = [{"type": "bind", "source": "/operator/config", "target": "/config", "read_only": True}]
    services["litellm"]["volumes"] = [
        {"type": "bind", "source": "/operator/policy.json", "target": "/run/factory-gateway/policy.json", "read_only": True},
        {"type": "bind", "source": "/operator/config/usecases", "target": "/factory-usecases", "read_only": True},
    ]
    services["grafana"]["environment"].update(
        GF_SERVER_ROOT_URL=f"https://grafana.{PRODUCTION_HOST}", GRAFANA_COOKIE_SECURE="auto", FACTORY_SCHEME="https",
    )
    for service in ("langfuse-web", "langfuse-worker"):
        services[service]["environment"]["ENCRYPTION_KEY"] = bytes(range(32)).hex()
        services[service]["environment"]["NEXTAUTH_URL"] = f"https://langfuse.{PRODUCTION_HOST}"
    return {"services": services, "networks": {"edge-identity": {"internal": True, "ipam": {
        "config": [{"subnet": "10.89.255.0/28", "ip_range": "10.89.255.8/29"}],
    }}}}


def test_valid_private_hostname_and_explicit_public_edge_bind(valid_config):
    assert production_errors(valid_config) == []


def test_production_allows_only_protected_portfolio_reviews(valid_config):
    api = valid_config["services"]["api"]["environment"]
    api["FACTORY_WORKBENCH_MODE"] = "portfolio"
    assert any("FACTORY_WORKBENCH_CYBER_AUDIT" in error for error in production_errors(valid_config))
    api["FACTORY_WORKBENCH_CYBER_AUDIT"] = "on"
    assert production_errors(valid_config) == []
    api["FACTORY_WORKBENCH_CHAT_MODE"] = "local"
    assert any("Workbench chat disabled" in error for error in production_errors(valid_config))


@pytest.mark.parametrize(("service", "key", "value", "label"), [
    ("edge", "FACTORY_HOST", "localhost", "FACTORY_HOST"),
    ("edge", "FACTORY_HOST", "demo.test", "FACTORY_HOST"),
    ("edge", "FACTORY_SCHEME", "http", "FACTORY_SCHEME"),
    ("edge", "FACTORY_AUTH", "none", "FACTORY_AUTH"),
    ("edge", "FACTORY_PRIVATE_TELEMETRY", "", "direct Grafana and Langfuse"),
    ("api", "FACTORY_AUTH_MODE", "none", "FACTORY_AUTH_MODE"),
    ("api", "FACTORY_BU_POLICY", "", "FACTORY_BU_POLICY"),
    ("api", "FACTORY_BU_POLICY", "/config/business-units.example.yaml", "FACTORY_BU_POLICY"),
    ("api", "FACTORY_BU_POLICY", "/config/../policy.yaml", "FACTORY_BU_POLICY"),
    ("api", "FACTORY_ROLE_MAP", "", "FACTORY_ROLE_MAP"),
    ("api", "FACTORY_ROLE_MAP", "staff:unknown", "FACTORY_ROLE_MAP"),
    ("api", "FACTORY_DEFAULT_ROLE", "admin", "FACTORY_DEFAULT_ROLE"),
    ("api", "FACTORY_TRUSTED_PROXIES", "10.89.255.0/28", "fixed edge IPv4"),
    ("api", "FACTORY_DEMO_MODE", "auto", "Disable demo"),
    ("api", "FACTORY_WORKBENCH_MODE", "simulation", "Disable demo"),
    ("oauth2-proxy", "OAUTH2_PROXY_TRUSTED_PROXY_IPS", "0.0.0.0/0", "fixed edge IPv4"),
    ("oauth2-proxy", "OAUTH2_PROXY_OIDC_ISSUER_URL", "http://idp.company.internal", "HTTPS issuer"),
    ("oauth2-proxy", "OAUTH2_PROXY_OIDC_ISSUER_URL", "https://idp.company.internal:bad", "HTTPS issuer"),
    ("oauth2-proxy", "OAUTH2_PROXY_INSECURE_OIDC_ALLOW_UNVERIFIED_EMAIL", "true", "verification of issuer"),
    ("oauth2-proxy", "OAUTH2_PROXY_INSECURE_OIDC_SKIP_ISSUER_VERIFICATION", "true", "verification of issuer"),
    ("oauth2-proxy", "OAUTH2_PROXY_SSL_INSECURE_SKIP_VERIFY", "true", "TLS certificates"),
    ("oauth2-proxy", "OAUTH2_PROXY_REDIRECT_URL", "https://other.company.internal/", "callback host"),
    ("oauth2-proxy", "OAUTH2_PROXY_CODE_CHALLENGE_METHOD", "plain", "PKCE"),
    ("oauth2-proxy", "OAUTH2_PROXY_COOKIE_SECURE", "false", "Secure, HttpOnly"),
    ("oauth2-proxy", "OAUTH2_PROXY_COOKIE_DOMAINS", "company.internal", "no Domain"),
    ("oauth2-proxy", "OAUTH2_PROXY_EMAIL_DOMAINS", "*", "Restrict SSO sign-in"),
    ("oauth2-proxy", "OAUTH2_PROXY_CLIENT_SECRET", "change-me-canary", "OAUTH2_PROXY_CLIENT_SECRET"),
    ("langfuse-web", "ENCRYPTION_KEY", "0" * 64, "langfuse-web.ENCRYPTION_KEY"),
    ("langfuse-worker", "ENCRYPTION_KEY", "not-hex", "langfuse-worker.ENCRYPTION_KEY"),
    ("langfuse-web", "NEXTAUTH_URL", "http://langfuse.localhost", "HTTPS LANGFUSE_PUBLIC_URL"),
    ("grafana", "GF_SERVER_ROOT_URL", "http://grafana.localhost", "HTTPS GRAFANA_PUBLIC_URL"),
    ("grafana", "GRAFANA_COOKIE_SECURE", "false", "secure Grafana cookies"),
    ("grafana", "FACTORY_SCHEME", "http", "secure Grafana cookies"),
    ("litellm", "FACTORY_GUARDRAIL_MODE", "flag", "blocking LiteLLM"),
    ("litellm", "FACTORY_GATEWAY_POLICY", "", "FACTORY_GATEWAY_POLICY"),
    ("litellm", "FACTORY_DEMO_MODE", "auto", "strict LiteLLM inference"),
    ("litellm", "LOCAL_CHAT_MODEL", "anthropic/remote-model", "LOCAL_CHAT_MODEL"),
    ("litellm", "LOCAL_EMBED_MODEL", "bedrock/remote-model", "LOCAL_EMBED_MODEL"),
    ("litellm", "LOCAL_CHAT_MODEL", "openai/", "LOCAL_CHAT_MODEL"),
    ("litellm", "FACTORY_ENFORCE_SENSITIVE_ROUTING", "false", "sensitive routing for C3 and C4"),
    ("litellm", "FACTORY_PII_REDACTION", "false", "PII redaction"),
    ("litellm", "FACTORY_SENSITIVE_CLASSES", "C4", "sensitive routing for C3 and C4"),
    ("litellm", "FACTORY_SENSITIVE_ALIAS", "chat-default", "alias allowlists"),
    ("litellm", "FACTORY_SENSITIVE_OK_ALIASES", "embed-default,chat-default", "alias allowlists"),
    ("litellm", "FACTORY_LOCAL_ALIASES", "chat-sensitive,embed-default,chat-default", "alias allowlists"),
    ("litellm", "LOCAL_API_BASE", "http://mock-llm:8080/local/v1", "LOCAL_API_BASE"),
    ("litellm", "LOCAL_API_BASE", "http://vllm-local:8000/v1", "LOCAL_API_BASE"),
    ("litellm", "LOCAL_API_BASE", "http://127.0.0.1:8000/v1", "LOCAL_API_BASE"),
    ("litellm", "LOCAL_API_BASE", "http://localhost.localdomain:8000/v1", "LOCAL_API_BASE"),
    ("litellm", "LOCAL_API_BASE", "http://0x08.0x08.0x08.0x08/v1", "LOCAL_API_BASE"),
    ("litellm", "LOCAL_API_BASE", "https://8.8.8.8/v1", "LOCAL_API_BASE"),
    ("litellm", "LOCAL_API_BASE", "https://api.openai.com/v1", "LOCAL_API_BASE"),
    ("litellm", "LOCAL_API_BASE", "http://169.254.169.254/v1", "LOCAL_API_BASE"),
    ("litellm", "LOCAL_EMBED_API_BASE", "https://user@gpu.company.internal/v1", "LOCAL_EMBED_API_BASE"),
    ("litellm", "LOCAL_EMBED_API_BASE", "https://gpu.company.internal/v1?key=value", "LOCAL_EMBED_API_BASE"),
])
def test_insecure_settings_fail(valid_config, service, key, value, label):
    valid_config["services"][service]["environment"][key] = value
    assert any(label in message for message in production_errors(valid_config))


def test_policy_mounts_must_be_read_only_and_unambiguous(valid_config):
    valid_config["services"]["api"]["volumes"][0]["read_only"] = False
    mounts = valid_config["services"]["litellm"]["volumes"]
    mounts.append(mounts[0].copy())
    errors = "\n".join(production_errors(valid_config))
    assert "FACTORY_BU_POLICY" in errors and "FACTORY_GATEWAY_POLICY" in errors


def test_portfolio_runner_requires_read_only_binding_and_single_internal_api(valid_config):
    api = valid_config["services"]["api"]
    target = "/run/secrets/factory_portfolio_runner"
    api["environment"].update(FACTORY_PORTFOLIO_RUNNER_FILE=target, LITELLM_URL="http://litellm:4000")
    assert any("runner binding file" in error for error in production_errors(valid_config))
    api["volumes"].append({"type": "bind", "source": "/operator/private-runner.json",
                           "target": target, "read_only": True})
    assert production_errors(valid_config) == []
    api["deploy"] = {"replicas": 2}
    assert any("one API replica" in error for error in production_errors(valid_config))
    api["deploy"] = {"replicas": 1}
    api["environment"]["LITELLM_URL"] = "https://untrusted.example"
    assert any("internal LiteLLM origin" in error for error in production_errors(valid_config))


def test_ai_assist_opt_in_requires_private_runner_mount_and_rate_limit(valid_config):
    api = valid_config["services"]["api"]
    target = "/run/secrets/factory_portfolio_runner"
    api["environment"].update(FACTORY_ASSIST_ENABLED="on", LITELLM_URL="http://litellm:4000")
    assert any("runner binding file" in error for error in production_errors(valid_config))
    api["environment"]["FACTORY_PORTFOLIO_RUNNER_FILE"] = target
    api["volumes"].append({"type": "bind", "source": "/operator/private-runner.json",
                           "target": target, "read_only": True})
    assert production_errors(valid_config) == []
    api["environment"]["FACTORY_RATE_LIMIT_PER_MIN"] = "0"
    assert any("rate limiting" in error for error in production_errors(valid_config))


def test_rejects_unintended_bindings(valid_config):
    services = valid_config["services"]
    services["edge"]["ports"][0].pop("host_ip")
    services["edge"]["ports"][1]["host_ip"] = "0.0.0.0"
    services["api"]["ports"] = [{"target": 8000}]
    services["mock-llm"] = {"network_mode": "host"}
    errors = "\n".join(production_errors(valid_config))
    assert "edge ports" in errors and "isolated Compose networks" in errors and "sim profile" in errors


@pytest.mark.parametrize("mode", ["service:edge", "container:external", "host"])
def test_identity_network_rejects_shared_or_host_network_namespaces(valid_config, mode):
    valid_config["services"]["postgres"]["network_mode"] = mode
    assert any("isolated Compose networks" in message for message in production_errors(valid_config))


def test_no_published_ports_allowed_for_an_isolated_edge(valid_config):
    del valid_config["services"]["edge"]["ports"]
    assert production_errors(valid_config) == []


def test_rendered_gpu_profile_service_names_are_supported(valid_config):
    services = valid_config["services"]
    services["vllm-local"] = {}
    services["vllm-embed"] = {}
    services["litellm"]["environment"].update(
        LOCAL_API_BASE="http://vllm-local:8000/v1", LOCAL_EMBED_API_BASE="http://vllm-embed:8000/v1",
    )
    assert production_errors(valid_config) == []


def test_identity_network_rejects_a_dynamic_trusted_address_and_unintended_peers(valid_config):
    identity = valid_config["networks"]["edge-identity"]
    identity["ipam"]["config"][0]["ip_range"] = "10.89.255.0/28"
    assert any("fixed edge IPv4" in message for message in production_errors(valid_config))
    identity["ipam"]["config"][0]["ip_range"] = "10.89.255.8/29"
    valid_config["services"]["postgres"]["networks"] = {"edge-identity": None}
    assert any("fixed edge IPv4" in message for message in production_errors(valid_config))


def _preflight(input_text, *arguments):
    return subprocess.run([sys.executable, str(PREFLIGHT), *arguments], input=input_text,
                          text=True, capture_output=True)


def test_gpu_dns_requires_an_exact_explicit_operator_allowlist(valid_config):
    gateway = valid_config["services"]["litellm"]["environment"]
    gateway["LOCAL_API_BASE"] = "https://chat.gpu.company.internal/v1"
    gateway["LOCAL_EMBED_API_BASE"] = "https://embedding.gpu.company.internal/v1"
    config = json.dumps(valid_config)
    assert _preflight(config).returncode == 1
    assert _preflight(config, "--private-gpu-host", "gpu.company.internal").returncode == 1
    result = _preflight(config, "--private-gpu-host", "chat.gpu.company.internal",
                        "--private-gpu-host", "embedding.gpu.company.internal")
    assert result.returncode == 0, result.stderr


def test_cli_errors_never_reveal_configuration_values(valid_config):
    canary = "change-me-secret-canary"
    valid_config["services"]["oauth2-proxy"]["environment"]["OAUTH2_PROXY_CLIENT_SECRET"] = canary
    result = _preflight(json.dumps(valid_config))
    assert result.returncode == 1
    assert "OAUTH2_PROXY_CLIENT_SECRET" in result.stderr
    assert canary not in result.stdout + result.stderr


@pytest.mark.parametrize("input_text", ["", "not valid JSON with a secret canary", "null", "[]", '{"services": []}'])
def test_cli_rejects_missing_or_malformed_input(input_text):
    result = _preflight(input_text)
    assert result.returncode == 1
    assert "Production preflight failed" in result.stderr
    assert "secret canary" not in result.stderr


def _render_compose(overrides):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is not installed")
    example = ROOT / ".env.example"
    example_names = set(re.findall(r"(?m)^([A-Z0-9_]+)=", example.read_text(encoding="utf-8")))
    env = {name: value for name, value in os.environ.items()
           if name not in example_names and not name.startswith(("COMPOSE_", "FACTORY_", "SSO_", "EDGE_"))}
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(example), "--profile", "sso", "config", "--format", "json"],
        cwd=ROOT, env=env | overrides, text=True, capture_output=True,
    )
    assert result.returncode == 0, "Compose configuration could not be rendered"
    return result.stdout


def test_rendered_sample_is_loopback_only_and_not_production():
    rendered = _render_compose({})
    ports = json.loads(rendered)["services"]["edge"]["ports"]
    assert {(port["target"], port["protocol"]) for port in ports} == {
        (80, "tcp"), (443, "tcp"), (443, "udp"), (3001, "tcp"), (3002, "tcp"),
    }
    assert all(port["host_ip"] == "127.0.0.1" for port in ports)
    result = _preflight(rendered)
    assert result.returncode == 1
    assert "ENCRYPTION_KEY" in result.stderr and "FACTORY_AUTH_MODE" in result.stderr


def test_rendered_production_fixture_passes():
    fixture_vars = (
        "SSO_CLIENT_ID", "SSO_CLIENT_SECRET", "SSO_COOKIE_SECRET", "LITELLM_MASTER_KEY", "LITELLM_SALT_KEY",
        "LANGFUSE_SALT", "LANGFUSE_NEXTAUTH_SECRET", "LANGFUSE_INIT_PROJECT_PUBLIC_KEY",
        "LANGFUSE_INIT_PROJECT_SECRET_KEY", "LANGFUSE_INIT_USER_PASSWORD", "GRAFANA_ADMIN_PASSWORD",
        "POSTGRES_PASSWORD", "LITELLM_DB_PASSWORD", "LANGFUSE_DB_PASSWORD", "CLICKHOUSE_PASSWORD",
        "VALKEY_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY",
    )
    rendered = _render_compose(dict.fromkeys(fixture_vars, FIXTURE_VALUE) | {
        "FACTORY_HOST": PRODUCTION_HOST, "FACTORY_SCHEME": "https", "FACTORY_AUTH": "sso",
        "FACTORY_AUTH_MODE": "proxy", "FACTORY_ROLE_MAP": "staff:viewer,governance:approver",
        "FACTORY_BU_POLICY": "/config/business-units.yaml", "FACTORY_GATEWAY_POLICY": "./private-gateway-policy.json",
        "FACTORY_DEMO_MODE": "never", "FACTORY_GUARDRAIL_MODE": "block", "EDGE_BIND": "0.0.0.0",
        "LANGFUSE_ENCRYPTION_KEY": bytes(range(32)).hex(),
        "GRAFANA_PUBLIC_URL": f"https://grafana.{PRODUCTION_HOST}",
        "LANGFUSE_PUBLIC_URL": f"https://langfuse.{PRODUCTION_HOST}",
        "LOCAL_API_BASE": "http://10.40.20.15:8000/v1", "LOCAL_EMBED_API_BASE": "http://[fd00::20]:8000/v1",
        "SSO_OIDC_ISSUER_URL": "https://idp.company.internal", "SSO_EMAIL_DOMAINS": "company.internal",
    })
    ports = json.loads(rendered)["services"]["edge"]["ports"]
    assert all(port["host_ip"] == ("0.0.0.0" if port["target"] in {80, 443} else "127.0.0.1") for port in ports)
    result = _preflight(rendered)
    assert result.returncode == 0, result.stderr
    assert "passed" in result.stdout
