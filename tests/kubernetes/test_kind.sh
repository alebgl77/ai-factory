#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Run from any directory. Requires Docker, the CI-pinned Helm/kind/kubectl, Python and OpenSSL.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

fixtures=tests/kubernetes
cluster=factory-kube-ci-$$
namespace=kube-test
node_image=kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5
work="$(mktemp -d)"
export KUBECONFIG="$work/kubeconfig"
forward_pids=()
umask 077

kc() { kubectl --namespace "$namespace" "$@"; }
cleanup() {
  local status=$?
  trap - EXIT
  if (( status != 0 )); then
    kc get pods,pvc,events -o wide >&2 || true
    for pod in $(kc get pod -o name); do
      kc logs "$pod" --all-containers --prefix --tail=60 >&2 || true
    done
    for log in "$work"/*.forward; do
      [[ ! -f "$log" ]] || cat "$log" >&2
    done
  fi
  for pid in "${forward_pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  kind delete cluster --name "$cluster" || status=1
  rm -rf "$work"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

forward() {
  local resource=$1 ports=$2 log="$work/${2%%:*}.forward"
  kubectl --namespace "$namespace" port-forward --address 127.0.0.1 "$resource" "$ports" > "$log" 2>&1 &
  forward_pids+=("$!")
  for _ in {1..30}; do
    if grep -q '^Forwarding from 127.0.0.1:' "$log"; then return 0; fi
    sleep 1
  done
  return 1
}

stop_forward() {
  local index=$((${#forward_pids[@]} - 1)) pid
  pid=${forward_pids[$index]}
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  unset 'forward_pids[index]'
}

factory_pod() {
  kc get pod -l "app.kubernetes.io/instance=$1,app.kubernetes.io/component=factory" -o jsonpath='{.items[0].metadata.name}'
}

python3 scripts/package_helm.py "$work"
chart="$work/ai-factory"
image_flags=(
  --set-string images.api.repository=ai-factory/api --set-string images.dashboard.repository=ai-factory/dashboard
  --set-string images.api.tag=kube-ci --set-string images.api.digest= --set-string images.api.pullPolicy=Never
  --set-string images.dashboard.tag=kube-ci --set-string images.dashboard.digest= --set-string images.dashboard.pullPolicy=Never
)
docker build --pull -t ai-factory/api:kube-ci api/
docker build --pull -t ai-factory/dashboard:kube-ci dashboard/
kind create cluster --name "$cluster" --image "$node_image" --wait 180s
kind load docker-image --name "$cluster" ai-factory/api:kube-ci ai-factory/dashboard:kube-ci
kubectl create namespace "$namespace"
kubectl label namespace "$namespace" pod-security.kubernetes.io/enforce=restricted \
  pod-security.kubernetes.io/enforce-version=v1.37

# Server schema and admission checks include the actual Restricted namespace.
for profile in demo private; do
  values=()
  if [[ "$profile" == private ]]; then values=(-f "$fixtures/private-values.yaml"); fi
  helm template "$profile" "$chart" --namespace "$namespace" "${values[@]}" "${image_flags[@]}" \
    | kc apply --dry-run=server --validate=strict -f -
done

deploy() {
  local release=$1
  shift
  helm upgrade --install "$release" "$chart" --namespace "$namespace" \
    "${image_flags[@]}" "$@" --wait --timeout 4m
  kc rollout status "deployment/$release-ai-factory" --timeout=120s
}

deploy demo
forward service/demo-ai-factory 18080:8080
python3 "$fixtures/demo_client.py" write "$work/demo.json"
stop_forward

pod="$(factory_pod demo)"
pod_uid="$(kc get pod "$pod" -o jsonpath='{.metadata.uid}')"
restart_count="$(kc get pod "$pod" -o jsonpath='{.status.containerStatuses[?(@.name=="api")].restartCount}')"
kc exec "$pod" -c api -- python -c 'import os, signal; os.kill(1, signal.SIGTERM)'
for _ in {1..45}; do
  count="$(kc get pod "$pod" -o jsonpath='{.status.containerStatuses[?(@.name=="api")].restartCount}')"
  (( count > restart_count )) && break
  sleep 2
done
(( count > restart_count ))
[[ "$(kc get pod "$pod" -o jsonpath='{.metadata.uid}')" == "$pod_uid" ]]
kc wait --for=condition=Ready "pod/$pod" --timeout=120s
forward service/demo-ai-factory 18080:8080
python3 "$fixtures/demo_client.py" after-container-restart "$work/demo.json"
stop_forward

kc delete pod "$pod" --timeout=120s
kc rollout status deployment/demo-ai-factory --timeout=120s
pod="$(factory_pod demo)"
[[ "$(kc get pod "$pod" -o jsonpath='{.metadata.uid}')" != "$pod_uid" ]]
forward service/demo-ai-factory 18080:8080
python3 "$fixtures/demo_client.py" after-pod-replacement "$work/demo.json"
stop_forward

# Quiesce the writer, take an archive, and restore to a different claim. Read back via the API.
kc scale deployment/demo-ai-factory --replicas=0
kc wait --for=delete "pod/$pod" --timeout=120s
kc create -f - <<'PVC'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: restored-state
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 1Gi
PVC
source_claim="$(kc get pvc -l app.kubernetes.io/instance=demo -o jsonpath='{.items[0].metadata.name}')"
[[ -n "$source_claim" && "$source_claim" != restored-state ]]
python3 "$fixtures/fixtures.py" restore "$source_claim" restored-state | kc create -f -
kc wait --for=condition=Ready pod/restore --timeout=120s
kc exec restore -- tar -C /source -cf - . > "$work/state.tar"
kc exec -i restore -- tar --no-same-owner --no-overwrite-dir -C /target -xf - < "$work/state.tar"
kc delete pod restore --timeout=120s
deploy restored --set persistence.existingClaim=restored-state
forward service/restored-ai-factory 18080:8080
python3 "$fixtures/demo_client.py" from-restored-pvc "$work/demo.json"
stop_forward

certificate() {
  local host=$1 name=$2
  openssl req -newkey rsa:2048 -nodes -x509 -days 1 -subj "/CN=$host" \
    -addext "subjectAltName=DNS:$host" -keyout "$work/$name.key" -out "$work/$name.crt" 2>/dev/null
}
certificate factory.test factory
certificate dex.kube-test.svc.cluster.local dex
kc create secret tls factory-test-tls --cert="$work/factory.crt" --key="$work/factory.key"
kc create secret tls dex-tls --cert="$work/dex.crt" --key="$work/dex.key"
kc create secret generic factory-test-ca --from-file="ca.crt=$work/dex.crt"
kc create secret generic factory-test-bu --from-file="business-units.yaml=$fixtures/business-units.yaml"
kc create secret generic factory-test-oidc --from-literal=client-id=factory-e2e \
  --from-literal="client-secret=$(openssl rand -hex 24)" \
  --from-literal="cookie-secret=$(openssl rand -base64 32 | tr '+/' '-_')"
kc create configmap dex --from-file="$fixtures/dex.yaml"
kc create configmap ingress --from-file="Caddyfile=$fixtures/Ingress.Caddyfile"
python3 "$fixtures/fixtures.py" dex | kc create -f -
kc expose pod dex --port=5556
caddy_image="$(helm template demo "$chart" "${image_flags[@]}" --show-only templates/deployment.yaml | python3 -c '
import sys, yaml
spec = yaml.safe_load(sys.stdin)["spec"]["template"]["spec"]
print(next(container["image"] for container in spec["containers"] if container["name"] == "edge"))
')"
python3 "$fixtures/fixtures.py" ingress "$caddy_image" | kc create -f -
python3 "$fixtures/fixtures.py" outsider | kc create -f -
kc wait --for=condition=Ready pod/dex pod/ingress pod/outsider --timeout=180s
deploy private -f "$fixtures/private-values.yaml"

pod="$(factory_pod private)"
pod_ip="$(kc get pod "$pod" -o jsonpath='{.status.podIP}')"
kc exec "$pod" -c api -- python -c '
import socket, sys, urllib.request
with urllib.request.urlopen("http://127.0.0.1:8000/api/v1/ready", timeout=3) as result:
    assert result.status == 200
try:
    with socket.create_connection((sys.argv[1], 8000), timeout=3):
        raise SystemExit("API accepted a Pod-IP connection")
except ConnectionRefusedError:
    print("PASS API listens only on loopback")
' "$pod_ip"
kc exec outsider -- python -c '
import socket, sys
for address in sys.argv[1:]:
    try:
        with socket.create_connection((address, 8080), timeout=4):
            raise SystemExit("NetworkPolicy admitted outsider traffic to " + address)
    except TimeoutError:
        print("PASS NetworkPolicy denies outsider traffic to " + address)
' "$pod_ip" private-ai-factory
forward pod/ingress 18443:8443
forward pod/dex 15556:5556
python3 "$fixtures/private_client.py" "$work/factory.crt" "$work/dex.crt"
