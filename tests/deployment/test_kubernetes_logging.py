# SPDX-License-Identifier: MIT
"""Verify the opt-in Kubernetes log collector and its isolation boundaries."""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy/helm/ai-factory"
EXAMPLE = CHART / "values-logging.example.yaml"
RELEASE = "factory-probe"
NAMESPACE = "factory-logs"
COLLECTOR_LABEL = "app.kubernetes.io/component"


@pytest.fixture(scope="module")
def helm_chart(tmp_path_factory):
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("Helm CLI unavailable")
    output = tmp_path_factory.mktemp("helm-logging-chart")
    subprocess.run([sys.executable, str(ROOT / "scripts/package_helm.py"), str(output)], check=True, timeout=60)
    return helm, output / CHART.name


def render(helm_chart, *values_args, namespace=NAMESPACE, check=True):
    helm, chart = helm_chart
    return subprocess.run(
        [helm, "template", RELEASE, str(chart), "--namespace", namespace, *values_args],
        capture_output=True,
        check=check,
        text=True,
        timeout=60,
    )


def resources(helm_chart, *values_args):
    return [doc for doc in yaml.safe_load_all(render(helm_chart, *values_args).stdout) if doc]


@pytest.fixture
def collector(helm_chart):
    return {
        doc["kind"]: doc
        for doc in resources(helm_chart, "-f", str(EXAMPLE))
        if doc["metadata"].get("labels", {}).get(COLLECTOR_LABEL) == "alloy"
    }


def test_no_collector_or_role_with_default_values(helm_chart):
    for resource in resources(helm_chart):
        assert resource["metadata"].get("labels", {}).get(COLLECTOR_LABEL) != "alloy"
        assert resource["kind"] not in {"Role", "RoleBinding", "ClusterRole", "ClusterRoleBinding"}


def test_collector_uses_namespace_rbac_and_a_locked_down_pod(collector):
    assert set(collector) == {"ServiceAccount", "Role", "RoleBinding", "ConfigMap", "Deployment", "NetworkPolicy"}
    assert {doc["metadata"]["namespace"] for doc in collector.values()} == {NAMESPACE}
    assert collector["Role"]["rules"] == [
        {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list", "watch"]},
        {"apiGroups": [""], "resources": ["pods/log"], "verbs": ["get"]},
    ]
    name = collector["Role"]["metadata"]["name"]
    assert collector["RoleBinding"]["roleRef"]["kind"] == "Role"
    assert collector["RoleBinding"]["roleRef"]["name"] == name
    assert collector["RoleBinding"]["subjects"] == [{"kind": "ServiceAccount", "name": name, "namespace": NAMESPACE}]
    assert collector["ServiceAccount"]["automountServiceAccountToken"] is False
    deployment = collector["Deployment"]["spec"]
    assert deployment["replicas"] == 1 and deployment["strategy"]["type"] == "Recreate"
    pod = deployment["template"]["spec"]
    assert pod["serviceAccountName"] == name and pod["automountServiceAccountToken"] is True
    assert pod["securityContext"] == {"fsGroup": 473, "seccompProfile": {"type": "RuntimeDefault"}}
    assert not pod.get("hostNetwork") and not pod.get("hostPID") and not pod.get("hostIPC")
    container, = pod["containers"]
    assert container["securityContext"] == {
        "runAsNonRoot": True,
        "runAsUser": 473,
        "runAsGroup": 473,
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert "--server.http.listen-addr=127.0.0.1:12345" in container["args"]
    assert "--disable-reporting" in container["args"]
    assert all("hostPath" not in volume for volume in pod["volumes"])
    assert all(volume["emptyDir"]["sizeLimit"] for volume in pod["volumes"] if "emptyDir" in volume)
    assert "docker.sock" not in yaml.safe_dump(collector)
    assert container["env"][0]["valueFrom"]["secretKeyRef"] == {"name": "factory-loki", "key": "username"}
    secret, = [volume["secret"] for volume in pod["volumes"] if "secret" in volume]
    assert secret["secretName"] == "factory-loki"
    assert secret["items"] == [{"key": "password", "path": "password"}, {"key": "ca.crt", "path": "ca.crt"}]


def test_egress_is_limited_to_the_operator_destinations(collector):
    policy = collector["NetworkPolicy"]["spec"]
    labels = collector["Deployment"]["spec"]["template"]["metadata"]["labels"]
    assert policy["podSelector"]["matchLabels"] == labels
    assert set(policy["policyTypes"]) == {"Ingress", "Egress"} and policy["ingress"] == []
    egress = policy["egress"]
    assert len(egress) == 4
    assert [egress[i]["to"][0]["ipBlock"]["cidr"] for i in (0, 1, 3)] == [
        "192.0.2.4/32", "192.0.2.5/32", "198.51.100.3/32",
    ]
    assert [egress[i]["ports"] for i in (0, 1, 3)] == [
        [{"protocol": "TCP", "port": 443}], [{"protocol": "TCP", "port": 6443}], [{"protocol": "TCP", "port": 443}],
    ]
    assert egress[2] == {
        "to": [{
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
            "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
        }],
        "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
    }


def test_pipeline_keeps_release_labels_and_matches_compose_scrubbing(collector):
    river = collector["ConfigMap"]["data"]["config.alloy"]
    compose_river = (ROOT / "compose/alloy/config.alloy").read_text(encoding="utf-8")
    assert f'names = ["{NAMESPACE}"]' in river
    assert (
        f'"app.kubernetes.io/component=factory,app.kubernetes.io/instance={RELEASE},app.kubernetes.io/name=ai-factory"'
        in river
    )
    assert 'target_label  = "service"' in river and 'target_label  = "project"' in river
    assert 'forward_to = [loki.process.factory.receiver]' in river
    assert 'forward_to = [loki.write.operator.receiver]' in river
    for stage in ("replace", "regex", "template", "labels"):
        pattern = rf"^  stage\.{stage} \{{\n.*?^  \}}"
        assert re.search(pattern, river, re.M | re.S).group() == re.search(pattern, compose_river, re.M | re.S).group()
    assert 'password_file = "/var/run/factory-loki/password"' in river
    assert 'ca_file     = "/var/run/factory-loki/ca.crt"' in river
    assert 'min_version = "TLS12"' in river and "insecure_skip_verify" not in river
    assert re.search(r"follow_redirects\s*= false", river)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ("logging.loki.url=http://loki.example.com/loki/api/v1/push", "HTTPS URL"),
        ("logging.loki.url=https://user@loki.example.com/loki/api/v1/push", "credential-free HTTPS URL"),
        ("logging.loki.existingSecret=", "existingSecret is required"),
        ("logging.egress.dnsNamespace=", "must be set together"),
        ("logging.egress.kubernetesAPI[0].cidr=0.0.0.0/0,logging.egress.kubernetesAPI[0].port=443", "non-default CIDRs"),
        ("logging.egress.loki[0].cidr=::/0,logging.egress.loki[0].port=443", "non-default CIDRs"),
        ("logging.egress.loki[0].cidr=198.51.100.3/32,logging.egress.loki[0].port=65536", "port"),
    ],
)
def test_unsafe_or_incomplete_logging_config_fails_at_render(helm_chart, override, message):
    result = render(helm_chart, "-f", str(EXAMPLE), "--set", override, check=False)
    assert result.returncode != 0 and message in result.stderr


def test_collector_cannot_be_enabled_in_a_built_in_namespace(helm_chart):
    result = render(helm_chart, "-f", str(EXAMPLE), namespace="default", check=False)
    assert result.returncode != 0 and "dedicated namespace" in result.stderr


def test_rendered_config_validates_in_pinned_alloy(collector, tmp_path):
    docker = shutil.which("docker")
    if docker is None or subprocess.run([docker, "info"], capture_output=True, timeout=20).returncode:
        pytest.skip("Docker daemon unavailable")
    path = tmp_path / "config.alloy"
    path.write_text(collector["ConfigMap"]["data"]["config.alloy"], encoding="utf-8", newline="\n")
    container, = collector["Deployment"]["spec"]["template"]["spec"]["containers"]
    result = subprocess.run(
        [
            docker, "run", "--rm", "--network", "none",
            "-e", "FACTORY_LOKI_USERNAME=fixture",
            "-v", f"{path}:/etc/alloy/config.alloy:ro",
            container["image"], "validate", "/etc/alloy/config.alloy",
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
