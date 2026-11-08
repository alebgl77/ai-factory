# SPDX-License-Identifier: MIT
"""Test the staged chart and package without a cluster."""

from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
HELM = shutil.which("helm")
pytestmark = pytest.mark.skipif(HELM is None, reason="Helm is required for chart rendering")


def helm(*args, check=True):
    return subprocess.run(
        [HELM, *map(str, args)], cwd=ROOT, check=check,
        capture_output=True, text=True, encoding="utf-8", timeout=45,
    )


@pytest.fixture(scope="module")
def chart(tmp_path_factory):
    directory = tmp_path_factory.mktemp("helm")
    subprocess.run([sys.executable, ROOT / "scripts/package_helm.py", directory], cwd=ROOT, check=True, timeout=45)
    return directory / "ai-factory"


def render(chart, *args):
    return {
        document["kind"]: document
        for document in yaml.safe_load_all(helm("template", "factory", chart, *args).stdout)
        if document
    }


@pytest.mark.parametrize("values", [(), ("-f", HERE / "private-values.yaml")], ids=["demo", "private"])
def test_package_and_validate_workload_security(chart, tmp_path, values):
    helm("lint", "--strict", chart, *values)
    helm("package", chart, "--destination", tmp_path, "--version", "9.9.9-ci", "--app-version", "v9.9.9-ci")
    package = tmp_path / "ai-factory-9.9.9-ci.tgz"
    assert package.is_file()
    documents = render(package, *values)
    deployment = documents["Deployment"]["spec"]
    assert deployment["replicas"] == 1 and deployment["strategy"]["type"] == "Recreate"
    pod = deployment["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert not any(pod.get(field, False) for field in ("hostNetwork", "hostPID", "hostIPC"))
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    for container in pod["containers"]:
        security = container["securityContext"]
        assert security["allowPrivilegeEscalation"] is False and security["readOnlyRootFilesystem"] is True
        assert "ALL" in security["capabilities"]["drop"]
        assert container["resources"]["requests"] and container["resources"]["limits"]
    assert all("hostPath" not in volume for volume in pod["volumes"])
    assert documents["PersistentVolumeClaim"]["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert documents["Service"]["spec"].get("type", "ClusterIP") == "ClusterIP"
    assert [port["port"] for port in documents["Service"]["spec"]["ports"]] == [8080]
    policy = documents["NetworkPolicy"]["spec"]
    assert set(policy["policyTypes"]) == {"Ingress", "Egress"}
    assert policy["podSelector"]["matchLabels"] == deployment["selector"]["matchLabels"]
    assert "Secret" not in documents
    if values:
        assert documents["Ingress"]["spec"]["tls"] == [{"hosts": ["factory.test"], "secretName": "factory-test-tls"}]
        assert [port["port"] for rule in policy["ingress"] for port in rule["ports"]] == [8080]
        assert not any(not peer for rule in policy["ingress"] for peer in rule["from"])
    else:
        assert "Ingress" not in documents and not policy.get("ingress") and not policy.get("egress")


@pytest.mark.parametrize("args", [
    ("--set", "profile=public"),
    ("--set", "profile=private"),
    ("--set", "ingress.enabled=true"),
    ("--set-string", "images.api.digest=sha256:bad"),
    ("--set", "notAValue=true"),
])
def test_invalid_or_incomplete_profiles_fail_render(chart, args):
    assert helm("template", "factory", chart, *args, check=False).returncode != 0


def test_existing_claim_is_never_reprovisioned(chart):
    documents = render(chart, "--set-string", "persistence.existingClaim=previous-state")
    assert "PersistentVolumeClaim" not in documents
    volumes = documents["Deployment"]["spec"]["template"]["spec"]["volumes"]
    assert any(volume.get("persistentVolumeClaim", {}).get("claimName") == "previous-state" for volume in volumes)
