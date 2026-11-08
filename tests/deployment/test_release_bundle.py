# SPDX-License-Identifier: MIT
"""Regression tests for the signed release deployment inventory and Compose overlay."""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("create_release_bundle", ROOT / ".github/scripts/create_release_bundle.py")
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)

SHA = "1" * 40
DIGEST = "sha256:" + "2" * 64
UPSTREAM_DIGEST = "sha256:" + "3" * 64
REGISTRY = "ghcr.io/example"


def test_inventory_reuses_the_same_digest_for_identical_upstream_images():
    lookups = []

    def resolve(image):
        lookups.append(image)
        return UPSTREAM_DIGEST

    services = {
        "api": {"image": "ai-factory/api:local", "build": {}},
        "edge": {"image": "caddy:2.11.7-alpine"},
        "volume-init": {"image": "caddy:2.11.7-alpine"},
        "postgres": {"image": f"postgres:17.11-alpine@{DIGEST}"},
    }
    refs = {"api": f"{REGISTRY}/api@{DIGEST}"}
    inventory = bundle.deployment_inventory(services, refs, REGISTRY, resolve)
    assert lookups == ["caddy:2.11.7-alpine"]
    assert inventory["api"]["origin"] == "release"
    assert inventory["edge"]["image"] == inventory["volume-init"]["image"] == f"caddy:2.11.7-alpine@{UPSTREAM_DIGEST}"
    assert inventory["postgres"]["image"] == f"postgres:17.11-alpine@{DIGEST}"


@pytest.mark.parametrize("refs", [
    {},
    {"api": f"{REGISTRY}/dashboard@{DIGEST}"},
    {"api": f"{REGISTRY}/api@sha256:broken"},
    {"api": f"{REGISTRY}/api@{DIGEST}", "unrelated": f"{REGISTRY}/unrelated@{DIGEST}"},
])
def test_missing_extra_swapped_or_invalid_release_artifacts_fail(refs):
    services = {"api": {"image": "ai-factory/api:local", "build": {}}}
    with pytest.raises(ValueError):
        bundle.deployment_inventory(services, refs, REGISTRY)


def test_unresolved_or_invalid_upstream_digest_fails():
    with pytest.raises(ValueError, match="did not resolve"):
        bundle.deployment_inventory({"edge": {"image": "caddy:2.11.7-alpine"}}, {}, REGISTRY, lambda _: "bad")


def test_helm_values_reuse_the_same_release_inventory(tmp_path):
    chart_assets = tmp_path / "deploy" / "helm"
    chart_assets.mkdir(parents=True)
    evidence = {"services": {
        "api": {"image": f"{REGISTRY}/api@{DIGEST}", "origin": "release"},
        "dashboard": {"image": f"{REGISTRY}/dashboard@{DIGEST}", "origin": "release"},
        "edge": {"image": f"caddy:2.11.7-alpine@{UPSTREAM_DIGEST}", "origin": "upstream"},
        "oauth2-proxy": {"image": f"quay.io/oauth2-proxy/oauth2-proxy:v7.15.6@{UPSTREAM_DIGEST}", "origin": "upstream"},
        "alloy": {"image": f"grafana/alloy:v1.21.3@{UPSTREAM_DIGEST}", "origin": "upstream"},
    }}
    inventory = tmp_path / "release-stack.json"
    inventory.write_text(json.dumps(evidence), encoding="utf-8")
    bundle.create_helm_values(tmp_path)
    assert json.loads((chart_assets / bundle.HELM_VALUES_NAME).read_text(encoding="utf-8")) == {
        "images": {
            "api": {"repository": f"{REGISTRY}/api", "digest": DIGEST},
            "dashboard": {"repository": f"{REGISTRY}/dashboard", "digest": DIGEST},
            "caddy": {"repository": "caddy", "digest": UPSTREAM_DIGEST},
            "oauth2Proxy": {"repository": "quay.io/oauth2-proxy/oauth2-proxy", "digest": UPSTREAM_DIGEST},
        },
        "logging": {"alloy": {"image": {"repository": "grafana/alloy", "digest": UPSTREAM_DIGEST}}},
    }
    evidence["services"]["dashboard"]["image"] = f"{REGISTRY}/api@{DIGEST}"
    inventory.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ValueError, match="dashboard"):
        bundle.create_helm_values(tmp_path)


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose is required for real config rendering")
def test_release_overlay_renders_every_profile_and_removes_every_build(tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    root.mkdir()
    assets = tmp_path / "assets"
    assets.mkdir()
    for filename in (".env.example", "docker-compose.yml", "docker-compose.portfolio.yml"):
        shutil.copyfile(ROOT / filename, root / filename)
    for name in ("api", "dashboard", "grafana-bootstrap", "mock-llm", "traffic", "gpu-sim"):
        (assets / f"{name}.ref").write_text(f"{REGISTRY}/{name}@{DIGEST}\n", encoding="utf-8")

    tag = "v9.0.0-rc.1"
    bundle.create_evidence(root, assets, tag, SHA, REGISTRY, lambda _: UPSTREAM_DIGEST)
    evidence = json.loads((root / "release-stack.json").read_text(encoding="utf-8"))
    assert evidence["tag"] == tag and evidence["source_commit"] == SHA and evidence["platform"] == "linux/amd64"
    assert evidence["services"]["oauth2-proxy"]["declared_image"] == "quay.io/oauth2-proxy/oauth2-proxy:v7.15.6"
    assert evidence["services"]["postgres"]["declared_image"] == "postgres:17.11-alpine"
    assert evidence["services"]["gpu-sim"]["profiles"] == ["sim"]
    langfuse_tags = {
        evidence["services"][service]["declared_image"].rsplit(":", 1)[-1]
        for service in ("langfuse-web", "langfuse-worker")
    }
    assert len(langfuse_tags) == 1

    monkeypatch.setenv("FACTORY_PORTFOLIO_RUNNER_SOURCE", str(tmp_path / "private-runner"))
    rendered = bundle.render_compose(root, bundle.LOCK_NAME, "docker-compose.portfolio.yml")
    assert set(rendered) == set(evidence["services"])
    assert all("build" not in service for service in rendered.values())
    assert any(
        volume.get("source") == "postgres-data" and volume.get("target") == "/var/lib/postgresql/data"
        for volume in rendered["postgres"]["volumes"]
    )
    assert rendered["api"]["environment"]["FACTORY_PORTFOLIO_RUNNER_FILE"] == "/run/secrets/factory_portfolio_runner"
