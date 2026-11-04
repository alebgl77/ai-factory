#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# End-to-end test of the "sso" profile, isolated from the running stack (own network, no
# published port): the shipped Caddyfile in FACTORY_AUTH=sso mode, oauth2-proxy configured
# with the exact environment rendered by `docker compose --profile sso config`, a test OpenID
# Connect provider (Dex, Apache-2.0, test only) and an echo service standing for the dashboard
# and the API.
#
#   compose/sso/test_sso_e2e.sh                  # default images (quay.io for oauth2-proxy)
#   OAUTH2_PROXY_IMAGE=<mirror>/oauth2-proxy:v7.15.5 compose/sso/test_sso_e2e.sh
#
# Checks: anonymous pages redirect to sign-in, anonymous API calls get 401, forged
# X-Forwarded-* headers never reach the API, the sign-in flow (PKCE) succeeds, the API sees the
# provider identity from the fixed edge address only, SSO_ALLOWED_GROUPS refuses other users.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERE="$ROOT/compose/sso/e2e"
cd "$ROOT"

NET=factory-sso-e2e
SUBNET=10.89.253.0/28
EDGE_IP=10.89.253.2
P=factory-sso-e2e
DEX_IMAGE=${DEX_IMAGE:-dexidp/dex:v2.46.1}
PY_IMAGE=${PY_IMAGE:-python:3.12.16-slim}
CADDY_IMAGE=$(docker compose config --format json | python3 -c "import json,sys; print(json.load(sys.stdin)['services']['edge']['image'])")

remove_containers() {
  docker rm -f "$P-edge" "$P-o2p" "$P-dex" "$P-echo" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
remove_containers
WORK="$(mktemp -d)"
trap 'remove_containers; rm -rf "$WORK"' EXIT

render_env() { # render_env ALLOWED_GROUPS -> oauth2-proxy environment as rendered by compose
  SSO_PROVIDER=oidc SSO_OIDC_ISSUER_URL=http://dex.test:5556/dex SSO_CLIENT_ID=factory-e2e \
  SSO_CLIENT_SECRET=e2e-client-secret-not-a-secret SSO_COOKIE_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(16))')" \
  SSO_EMAIL_DOMAINS='*' SSO_ALLOWED_GROUPS="$1" SSO_SCOPE="openid email profile groups" \
  SSO_COOKIE_SECURE=false SSO_COOKIE_NAME=_factory_sso FACTORY_HOST=factory.test FACTORY_EDGE_IDENTITY_IP=$EDGE_IP \
    docker compose --profile sso config --format json | python3 -c "
import json, sys
svc = json.load(sys.stdin)['services']['oauth2-proxy']
print(svc['image'])
open(sys.argv[1], 'w').write(''.join(f'{k}={v}\n' for k, v in svc['environment'].items()))" "$WORK/o2p.env"
}

start_o2p() {
  docker rm -f "$P-o2p" >/dev/null 2>&1 || true
  docker run -d --name "$P-o2p" --network "$NET" --network-alias oauth2-proxy --user 65532:65532 \
    --read-only --cap-drop ALL --security-opt no-new-privileges:true --env-file "$WORK/o2p.env" \
    "${OAUTH2_PROXY_IMAGE:-$1}" >/dev/null
  for _ in $(seq 1 30); do
    docker logs "$P-o2p" 2>&1 | grep -q "OAuthProxy configured" && return 0
    sleep 1
  done
  docker logs "$P-o2p" >&2
  return 1
}

client() { # client EXPECT
  docker run --rm --network "$NET" -e EDGE_IP=$EDGE_IP -e EXPECT="$1" -v "$HERE/client.py:/client.py:ro" "$PY_IMAGE" python /client.py
}

docker network create --internal --subnet $SUBNET --ip-range 10.89.253.8/29 "$NET" >/dev/null
docker run -d --name "$P-dex" --network "$NET" --network-alias dex.test \
  -v "$HERE/dex.yaml:/etc/dex/config.yaml:ro" "$DEX_IMAGE" dex serve /etc/dex/config.yaml >/dev/null
docker run -d --name "$P-echo" --network "$NET" --network-alias api-identity --network-alias dashboard \
  -v "$HERE/echo_upstream.py:/echo.py:ro" "$PY_IMAGE" python /echo.py >/dev/null
docker run -d --name "$P-edge" --network "$NET" --ip $EDGE_IP --network-alias factory.test \
  --user 10001:10001 --cap-drop ALL --cap-add NET_BIND_SERVICE --security-opt no-new-privileges:true --read-only \
  --tmpfs /data:uid=10001 --tmpfs /config:uid=10001 \
  -e FACTORY_SCHEME=http -e FACTORY_HOST=factory.test -e FACTORY_AUTH=sso \
  -v "$ROOT/compose/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" "$CADDY_IMAGE" >/dev/null

rc=0
echo "== sso e2e: member of SSO_ALLOWED_GROUPS"
start_o2p "$(render_env "authors,ai-governance")"
client allowed || rc=1
echo "== sso e2e: user outside SSO_ALLOWED_GROUPS"
start_o2p "$(render_env "ai-governance")"
client denied || rc=1
[ "$rc" -eq 0 ] && echo "sso e2e: all checks passed" || echo "sso e2e: FAILED"
exit "$rc"
