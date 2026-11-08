# SPDX-License-Identifier: MIT
"""Regression tests for shared assets and private Helm policy guards."""

import base64
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from test_chart import HERE, ROOT, chart, helm, pytestmark


def test_shared_assets_match_the_shipped_files(chart):
    manifests = list(yaml.safe_load_all(helm("template", "factory", chart).stdout))
    maps = [doc["binaryData"] for doc in manifests if doc and doc["kind"] == "ConfigMap"]
    edge = next(data for data in maps if "Caddyfile" in data)
    config = next(data for data in maps if "factory.yaml" in data)
    caddy = (ROOT / "compose/caddy/Caddyfile").read_bytes().replace(b"\r\n", b"\n")
    nginx = (ROOT / "dashboard/nginx.conf").read_bytes().replace(b"\r\n", b"\n")
    case = (ROOT / "config/usecases/uc-01.yaml").read_bytes().replace(b"\r\n", b"\n")
    assert base64.b64decode(edge["Caddyfile"]) == caddy
    assert base64.b64decode(edge["nginx.conf"]) == nginx.replace(b"listen 80;", b"listen 127.0.0.1:8081;")
    assert base64.b64decode(config["usecases__uc-01.yaml"]) == case


def test_unstaged_source_fails_with_packaging_instructions():
    result = helm("template", "factory", ROOT / "deploy/helm/ai-factory", check=False)
    assert result.returncode != 0
    assert "scripts/package_helm.py OUTPUT_DIR" in result.stderr


def test_staging_keeps_existing_output_intact(chart):
    before = (chart / "Chart.yaml").read_bytes()
    result = subprocess.run(
        [sys.executable, ROOT / "scripts/package_helm.py", chart.parent],
        cwd=ROOT, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode != 0 and "target already exists" in result.stderr
    assert (chart / "Chart.yaml").read_bytes() == before


@pytest.mark.parametrize(("override", "reason"), [
    ({"allowMutableImagesForLocalTesting": False}, "private requires images."),
    ({"networkPolicy": {"ingressPeers": [{"namespaceSelector": {"matchLabels": {}}}]}},
     "selectors cannot match every namespace or pod"),
    ({"networkPolicy": {"ingressPeers": [{"ipBlock": {"cidr": "0.0.0.0/0"}}]}},
     "CIDR narrower than /0"),
    ({"sso": {"allowedEmailDomains": ["*,factory.test"]}}, "individual domain names"),
    ({"telemetry": {"allowInsecureClusterHTTP": True, "litellmURL": "http://llm.example.test"}},
     "HTTP requires telemetry.allowInsecureClusterHTTP and an in-cluster Service FQDN"),
    ({"telemetry": {"allowInsecureClusterHTTP": True, "grafanaPublicURL": "http://grafana.tools.svc"}},
     "grafanaPublicURL must use HTTPS"),
])
def test_private_guard_rejects_insecure_values(chart, tmp_path: Path, override, reason):
    values = tmp_path / "override.yaml"
    values.write_text(yaml.safe_dump(override), encoding="utf-8")
    result = helm("template", "factory", chart, "-f", HERE / "private-values.yaml", "-f", values, check=False)
    assert result.returncode != 0
    assert reason in result.stderr


def test_private_runner_mounts_only_key_references(chart, tmp_path: Path):
    values = tmp_path / "runner.yaml"
    values.write_text(yaml.safe_dump({
        "api": {"assistEnabled": True, "portfolioRunner": {"existingSecret": "runner"}},
        "telemetry": {
            "litellmURL": "https://litellm:4000",
            "caBundle": {"existingSecret": "private-model-ca"},
        },
    }), encoding="utf-8")
    docs = yaml.safe_load_all(helm(
        "template", "factory", chart, "-f", HERE / "private-values.yaml", "-f", values
    ).stdout)
    pod = next(doc for doc in docs if doc and doc["kind"] == "Deployment")["spec"]["template"]["spec"]
    api = next(container for container in pod["containers"] if container["name"] == "api")
    env = {entry["name"]: entry.get("value") for entry in api["env"]}
    volumes = {volume["name"]: volume for volume in pod["volumes"]}
    assert env["FACTORY_ASSIST_ENABLED"] == "on"
    assert env["LITELLM_URL"] == "https://litellm:4000"
    assert "--no-proxy-headers" in api["command"]
    assert volumes["portfolio-runner"]["secret"]["secretName"] == "runner"
    assert volumes["telemetry-ca"]["secret"]["secretName"] == "private-model-ca"
    assert all(mount["readOnly"] for mount in api["volumeMounts"]
               if mount["name"] in {"portfolio-runner", "telemetry-ca"})
