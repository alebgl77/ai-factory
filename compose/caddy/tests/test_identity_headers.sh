#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Identity-header and CSP test of the shipped Caddyfile, for FACTORY_AUTH=none, basic and sso,
# isolated from the running stack (own internal network, no published port, nothing pulled but
# the edge image of docker-compose.yml and the Python image of the API).
#
#   compose/caddy/tests/test_identity_headers.sh [none|basic|sso ...]   # default: all three
#
# A stand-in upstream (upstream.py) answers for the dashboard, the API, Grafana, Langfuse and
# oauth2-proxy and echoes what the edge forwards. client.py sends forged X-Forwarded-User /
# -Email / -Groups / -Preferred-Username, X-Auth-Request-* and client-address headers to every
# route and checks that the upstreams only ever see the identity set by the edge itself:
# nothing in none mode, the basic-auth user in basic mode, the forward_auth answer in sso mode.
# It also checks that the dashboard CSP is never applied to /api/* and that the API's own CSP
# (OpenAPI page) passes untouched.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
HERE="$ROOT/compose/caddy/tests"
cd "$ROOT"

NET=factory-idh-test
SUBNET=10.89.252.0/28
EDGE_IP=10.89.252.2
EDGE_HEALTH=http://127.0.0.1:2018/healthz
P=factory-idh-test
PY_IMAGE=${PY_IMAGE:-python:3.12.16-slim}
CADDY_IMAGE=${CADDY_IMAGE:-$(docker compose config --format json | python3 -c "import json,sys; print(json.load(sys.stdin)['services']['edge']['image'])")}
BASIC_USER=factory-test
BASIC_PASSWORD="idh-$(python3 -c 'import secrets; print(secrets.token_hex(12))')"
MODES=("$@")
[ ${#MODES[@]} -gt 0 ] || MODES=(none basic sso)

cleanup() {
  docker rm -f "$P-edge" "$P-up" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
cleanup
trap cleanup EXIT

docker network create --internal --subnet "$SUBNET" --ip-range 10.89.252.8/29 "$NET" >/dev/null
docker run -d --name "$P-up" --network "$NET" \
  --network-alias api-identity --network-alias dashboard --network-alias grafana \
  --network-alias langfuse-web --network-alias oauth2-proxy \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true --user 65534:65534 \
  -v "$HERE/upstream.py:/upstream.py:ro" "$PY_IMAGE" python /upstream.py >/dev/null
BASIC_HASH="$(docker run --rm --network none "$CADDY_IMAGE" caddy hash-password --plaintext "$BASIC_PASSWORD")"

rc=0
for mode in "${MODES[@]}"; do
  echo "== edge identity headers and CSP: FACTORY_AUTH=$mode"
  docker rm -f "$P-edge" >/dev/null 2>&1 || true
  docker run -d --name "$P-edge" --network "$NET" --ip "$EDGE_IP" \
    --user 10001:10001 --cap-drop ALL --cap-add NET_BIND_SERVICE --security-opt no-new-privileges:true \
    --read-only --tmpfs /data:uid=10001 --tmpfs /config:uid=10001 \
    -e FACTORY_SCHEME=http -e FACTORY_HOST=factory.test -e FACTORY_AUTH="$mode" \
    -e FACTORY_BASIC_AUTH_USER="$BASIC_USER" -e FACTORY_BASIC_AUTH_HASH="$BASIC_HASH" \
    -v "$ROOT/compose/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" "$CADDY_IMAGE" >/dev/null
  ready=0
  for _ in $(seq 1 30); do
    if health="$(docker exec "$P-edge" wget -q -O - "$EDGE_HEALTH" 2>/dev/null)" && [ "$health" = ok ]; then
      ready=1
      break
    fi
    sleep 1
  done
  if [ "$ready" -ne 1 ]; then
    echo "edge health check failed ($mode): $EDGE_HEALTH" >&2
    docker logs "$P-edge" 2>&1 | tail -n 20 >&2 || true
    rc=1
    continue
  fi
  if ! docker exec "$P-up" python -c \
    'import socket, sys; s = socket.socket(); s.settimeout(2); sys.exit(s.connect_ex((sys.argv[1], 2018)) == 0)' "$EDGE_IP"; then
    echo "edge health listener is reachable from the peer network ($mode)" >&2
    rc=1
  fi
  docker run --rm --network "$NET" -e EDGE_IP="$EDGE_IP" -e MODE="$mode" \
    -e BASIC_USER="$BASIC_USER" -e BASIC_PASSWORD="$BASIC_PASSWORD" \
    -v "$HERE/client.py:/client.py:ro" "$PY_IMAGE" python /client.py || { rc=1; docker logs "$P-edge" 2>&1 | tail -n 20 >&2; }
done
[ "$rc" -eq 0 ] && echo "identity headers: all checks passed (${MODES[*]})" || echo "identity headers: FAILED"
exit "$rc"
