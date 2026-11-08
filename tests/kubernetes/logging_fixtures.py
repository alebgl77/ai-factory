# SPDX-License-Identifier: MIT
"""Restricted Loki sink and log producers for the kind test."""

import json
import sys

from fixtures import API_IMAGE, pod


def mount(name, path, read_only=False):
    return {"name": name, "mountPath": path, "readOnly": read_only}


def sink(loki_image, caddy_image):
    result = pod(
        "loki-sink", loki_image, 10001, ["/usr/bin/loki", "-config.file=/fixture/loki.yml"],
        [mount("fixture", "/fixture", True), mount("data", "/loki"), mount("tmp", "/tmp")],
        [{"name": "fixture", "configMap": {"name": "loki-sink"}},
         {"name": "tls", "secret": {"secretName": "loki-sink-tls", "defaultMode": 0o440}},
         *({"name": name, "emptyDir": {"sizeLimit": size}}
           # The TLS proxy copies Caddy to /tmp. Use the same bounded capacity as the chart's edge.
           for name, size in [("data", "128Mi"), ("tmp", "128Mi"),
                              ("caddy-data", "16Mi"), ("caddy-config", "16Mi")])],
    )
    caddy = pod(
        "proxy", caddy_image, 10001,
        ["/bin/sh", "-ec", "cp /usr/bin/caddy /tmp/loki-caddy; "
         "exec /tmp/loki-caddy run --config /fixture/Caddyfile --adapter caddyfile"],
        [mount("fixture", "/fixture", True), mount("tls", "/tls", True),
         mount("tmp", "/tmp"), mount("caddy-data", "/data"), mount("caddy-config", "/config")],
        ports=[{"containerPort": 8443}],
        readinessProbe={"tcpSocket": {"port": 8443}, "periodSeconds": 2},
    )
    result["spec"]["containers"].extend(caddy["spec"]["containers"])
    return result


def producer(release, marker):
    script = (
        "import sys,time\n"
        "for _ in range(8):\n"
        " print(f'level=INFO {sys.argv[1]} release={sys.argv[2]} sk-syntheticprobe123', flush=True)\n"
        " time.sleep(2)\n"
        "time.sleep(60)\n"
    )
    result = pod(f"producer-{release}", API_IMAGE, 10001, ["python", "-c", script, marker, release])
    result["spec"]["containers"][0]["name"] = "log-probe"
    result["spec"]["restartPolicy"] = "Never"
    result["metadata"]["labels"].update({
        "app.kubernetes.io/name": "ai-factory",
        "app.kubernetes.io/instance": release,
        "app.kubernetes.io/component": "factory",
    })
    return result


if __name__ == "__main__":
    match sys.argv[1:]:
        case ["sink", loki_image, caddy_image]:
            print(json.dumps(sink(loki_image, caddy_image)))
        case ["producer", release, marker]:
            print(json.dumps(producer(release, marker)))
        case _:
            sys.exit("Usage: logging_fixtures.py sink LOKI_IMAGE CADDY_IMAGE | producer RELEASE MARKER")
