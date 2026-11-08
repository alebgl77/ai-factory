# SPDX-License-Identifier: MIT
"""Build Restricted Pod fixtures for the disposable cluster."""

import json
import sys

API_IMAGE = "ai-factory/api:kube-ci"
DEX_IMAGE = "dexidp/dex:v2.46.1@sha256:65b72ca0f4b0bf6d4ada84ec21da8851782563404ac7b8f926923d27ceced9ab"


def pod(name, image, uid, command, mounts=(), volumes=(), **container_options):
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "labels": {"factory-test": name}},
        "spec": {
            "automountServiceAccountToken": False,
            "securityContext": {
                "runAsNonRoot": True, "runAsUser": uid, "runAsGroup": uid,
                "fsGroup": uid, "seccompProfile": {"type": "RuntimeDefault"},
            },
            "volumes": list(volumes),
            "containers": [{
                "name": name, "image": image, "imagePullPolicy": "IfNotPresent", "command": command,
                "securityContext": {
                    "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]},
                    "readOnlyRootFilesystem": True,
                },
                "volumeMounts": list(mounts),
                **container_options,
            }],
        },
    }


def dex():
    return pod(
        "dex", DEX_IMAGE, 1001, ["dex", "serve", "/fixture/dex.yaml"],
        [{"name": "config", "mountPath": "/fixture", "readOnly": True},
         {"name": "tls", "mountPath": "/tls", "readOnly": True}],
        [{"name": "config", "configMap": {"name": "dex"}},
         {"name": "tls", "secret": {"secretName": "dex-tls"}}],
        ports=[{"containerPort": 5556}],
        env=[{"name": "DEX_CLIENT_SECRET", "valueFrom": {
            "secretKeyRef": {"name": "factory-test-oidc", "key": "client-secret"},
        }}],
        readinessProbe={"tcpSocket": {"port": 5556}, "periodSeconds": 2},
    )


def ingress(image):
    return pod(
        "ingress", image, 10001, ["/bin/sh", "-ec",
                                "cp /usr/bin/caddy /tmp/caddy-bin; "
                                "exec /tmp/caddy-bin run --config /fixture/Caddyfile --adapter caddyfile"],
        [{"name": "config", "mountPath": "/fixture", "readOnly": True},
         {"name": "tls", "mountPath": "/tls", "readOnly": True},
         {"name": "data", "mountPath": "/data"},
         {"name": "caddy-config", "mountPath": "/config"}, {"name": "tmp", "mountPath": "/tmp"}],
        [{"name": "config", "configMap": {"name": "ingress"}},
         {"name": "tls", "secret": {"secretName": "factory-test-tls"}},
         {"name": "data", "emptyDir": {}}, {"name": "caddy-config", "emptyDir": {}},
         {"name": "tmp", "emptyDir": {}}],
        ports=[{"containerPort": 8443}],
        readinessProbe={"tcpSocket": {"port": 8443}, "periodSeconds": 2},
    )


def restore(source, target):
    return pod(
        "restore", API_IMAGE, 10001, ["sleep", "infinity"],
        [{"name": "source", "mountPath": "/source", "readOnly": True},
         {"name": "target", "mountPath": "/target"}],
        [{"name": "source", "persistentVolumeClaim": {"claimName": source, "readOnly": True}},
         {"name": "target", "persistentVolumeClaim": {"claimName": target}}],
    )


if __name__ == "__main__":
    match sys.argv[1:]:
        case ["dex"]:
            result = dex()
        case ["ingress", image]:
            result = ingress(image)
        case ["restore", source, target]:
            result = restore(source, target)
        case ["outsider"]:
            result = pod("outsider", API_IMAGE, 10001, ["sleep", "infinity"])
        case _:
            sys.exit("Usage: fixtures.py dex|ingress IMAGE|restore SOURCE TARGET|outsider")
    print(json.dumps(result))
