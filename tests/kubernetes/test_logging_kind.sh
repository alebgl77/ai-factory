#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Run only on the disposable cluster from test_kind.sh.
set -euo pipefail
chart=${1:?staged Helm chart is required}
namespace=${2:?restricted namespace is required}
[[ -n "${KUBECONFIG:-}" && "$(kubectl config current-context)" == kind-factory-kube-ci-* ]]
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
work="$(mktemp -d)"
umask 077
release=log-probe
collector="$release-ai-factory-alloy"
fixtures=tests/kubernetes/logging_fixtures.py
forward_pid=""
kc() { kubectl --namespace "$namespace" "$@"; }
cleanup() {
  local status=$?
  if (( status != 0 )); then
    kc describe pod loki-sink >&2 || true
    kc logs loki-sink --all-containers --prefix --tail=25 >&2 || true
    kc logs "deployment/$collector" --tail=50 >&2 || true
    cat "$work/forward.log" >&2 2>/dev/null || true
  fi
  [[ -z "$forward_pid" ]] || kill "$forward_pid" 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT
trap 'exit 143' TERM

loki_host="loki-sink.$namespace.svc.cluster.local"
password="$(openssl rand -hex 16)"
openssl req -newkey rsa:2048 -nodes -x509 -days 1 -subj "/CN=$loki_host" \
  -addext "subjectAltName=DNS:$loki_host" -keyout "$work/loki.key" -out "$work/loki.crt" 2>/dev/null
kc create secret tls loki-sink-tls --cert="$work/loki.crt" --key="$work/loki.key"
kc create secret generic loki-sink-auth --from-literal=username=probe --from-literal="password=$password" \
  --from-file="ca.crt=$work/loki.crt"

hash="$(kc exec pod/ingress -- /tmp/caddy-bin hash-password --plaintext "$password")"
cat > "$work/Caddyfile" <<EOF
{
  admin off
  auto_https disable_redirects
  default_sni $loki_host
}
https://$loki_host:8443 {
  tls /tls/tls.crt /tls/tls.key
  basic_auth {
    probe $hash
  }
  reverse_proxy 127.0.0.1:3100
}
EOF
read -r loki_image caddy_image < <(python3 - "$work/loki.yml" <<'PY'
import sys
from pathlib import Path
import yaml

services = yaml.safe_load(Path("docker-compose.yml").read_text())["services"]
config = yaml.safe_load(Path("compose/loki/loki.yml").read_text())
config["server"].update({"http_listen_address": "127.0.0.1", "grpc_listen_address": "127.0.0.1"})
Path(sys.argv[1]).write_text(yaml.safe_dump(config))
print(services["loki"]["image"], services["edge"]["image"])
PY
)
kc create configmap loki-sink --from-file="$work/Caddyfile" --from-file="$work/loki.yml"
python3 "$fixtures" sink "$loki_image" "$caddy_image" | kc create -f -
kc expose pod loki-sink --port=8443
kc wait --for=condition=Ready pod/loki-sink --timeout=70s

api_service="$(kubectl -n default get service kubernetes -o jsonpath='{.spec.clusterIP}')"
api_peer="$(kubectl -n default get endpointslice -l kubernetes.io/service-name=kubernetes -o jsonpath='{.items[0].endpoints[0].addresses[0]}')"
api_port="$(kubectl -n default get endpointslice -l kubernetes.io/service-name=kubernetes -o jsonpath='{.items[0].ports[0].port}')"
loki_service="$(kc get service loki-sink -o jsonpath='{.spec.clusterIP}')"
loki_peer="$(kc get pod loki-sink -o jsonpath='{.status.podIP}')"
cat > "$work/logging.yaml" <<EOF
logging:
  enabled: true
  loki:
    url: https://$loki_host:8443/loki/api/v1/push
    existingSecret: loki-sink-auth
    caKey: ca.crt
  egress:
    kubernetesAPI:
      - {cidr: $api_service/32, port: 443}
      - {cidr: $api_peer/32, port: $api_port}
    dnsNamespace: kube-system
    dnsPodSelector: {k8s-app: kube-dns}
    loki:
      - {cidr: $loki_service/32, port: 8443}
      - {cidr: $loki_peer/32, port: 8443}
EOF
helm template "$release" "$chart" --namespace "$namespace" -f "$work/logging.yaml" \
  --show-only templates/logging.yaml | kc create -f -
kc rollout status "deployment/$collector" --timeout=55s
actor="system:serviceaccount:$namespace:$collector"
kc auth can-i get pods --subresource=log --as="$actor" --quiet
if kc auth can-i get secrets --as="$actor" --quiet ||
   kubectl -n kube-system auth can-i get pods --subresource=log --as="$actor" --quiet; then
  echo 'Alloy service account has unexpected privileges' >&2
  exit 1
fi

kubectl --namespace "$namespace" port-forward --address 127.0.0.1 pod/loki-sink 13100:8443 > "$work/forward.log" 2>&1 &
forward_pid=$!
for _ in {1..20}; do
  grep -q '^Forwarding from 127.0.0.1:' "$work/forward.log" && break
  sleep 0.25
done
curl_tls=(curl --silent --show-error --noproxy '*' --max-time 3 --cacert "$work/loki.crt"
  --resolve "$loki_host:13100:127.0.0.1")
ready_url="https://$loki_host:13100/ready"
proxy_status=""
for _ in {1..10}; do
  if proxy_status="$("${curl_tls[@]}" --output /dev/null --write-out '%{http_code}' "$ready_url")" &&
     [[ "$proxy_status" == 401 ]]; then
    break
  fi
  sleep 1
done
[[ "$proxy_status" == 401 ]]
for _ in {1..15}; do
  if "${curl_tls[@]}" --fail --user "probe:$password" "$ready_url" > /dev/null; then break; fi
  sleep 1
done
"${curl_tls[@]}" --fail --user "probe:$password" "$ready_url" > /dev/null

marker="factory-kind-$(openssl rand -hex 8)"
for project in "$release" outside; do
  python3 "$fixtures" producer "$project" "$marker" | kc create -f -
done
kc wait --for=condition=Ready pod/producer-outside --timeout=15s
for _ in {1..5}; do
  if kc logs pod/producer-outside | grep -Fq "$marker"; then break; fi
  sleep 1
done
if ! kc logs pod/producer-outside | grep -Fq "$marker"; then
  echo 'The excluded producer emitted no canary lines' >&2
  exit 1
fi
query() {
  "${curl_tls[@]}" --fail --user "probe:$password" --get "https://$loki_host:13100/loki/api/v1/query_range" \
    --data-urlencode "query={factory=\"ai-factory-reference\"} |= \"$marker\""
}
for _ in {1..15}; do
  if query > "$work/query.json" &&
     jq -e --arg release "$release" \
       'any(.data.result[]; .stream.project == $release and .stream.level == "info" and (.values | length >= 3))' \
       "$work/query.json" > /dev/null; then
    break
  fi
  sleep 2
done
query > "$work/query.json"
# The Kubernetes API log tailer can preserve the line-ending newline.
jq --arg release "$release" --arg marker "$marker" '
  [.data.result[].values[][1] | sub("\r?\n$"; "")] as $lines |
  {
    streams: (.data.result | length),
    entries: ($lines | length),
    labels_ok: all(.data.result[];
      .stream.project == $release and .stream.service == "log-probe" and .stream.level == "info"),
    content_ok: all($lines[]; . == ("level=INFO " + $marker + " release=" + $release + " ***")),
    raw_canary_absent: all($lines[]; contains("sk-syntheticprobe123") | not),
    outside_absent: all($lines[]; contains("release=outside") | not)
  }
' "$work/query.json" > "$work/result.json"
if ! jq -e '.entries >= 3 and .labels_ok and .content_ok and .raw_canary_absent and .outside_absent' \
    "$work/result.json" > /dev/null; then
  jq -c . "$work/result.json" >&2
  exit 1
fi
echo 'PASS Alloy sends only the selected release, with service/project labels and redacted content, to TLS/Basic Auth Loki'
