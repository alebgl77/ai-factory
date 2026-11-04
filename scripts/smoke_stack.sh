#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# End-to-end smoke test of the deployable stack in key-less demo mode (profile "sim").
#
#   scripts/smoke_stack.sh [--no-up] [--no-build] [--down] [--timeout SECONDS]
#
#   --no-up      only run the checks against a stack that is already running
#   --no-build   start without rebuilding the local images
#   --down       stop the stack at the end (docker compose down; images and volumes kept)
#   --timeout    seconds to wait for the services to become healthy (default 900)
#
# Checks: every service healthy; edge, dashboard (vendored Three.js, CSP) and API health;
# snapshot fed by live sources with live GPU telemetry; Prometheus targets up and gateway
# metrics present; Grafana dashboards provisioned and reachable server side by the API;
# LiteLLM routes chat-default (cloud), falls back to the VPS when the cloud fails,
# serves chat-sensitive on the local GPU only and reroutes C3/C4 requests; Langfuse
# healthy with traces (sensitive ones masked); Loki ready with container logs; the Grafana
# token never appears in what the browser receives.
# Exit code: 0 when every check passes, 1 otherwise.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

DO_UP=1
BUILD="--build"
DOWN=0
TIMEOUT=900
while [ $# -gt 0 ]; do
  case "$1" in
    --no-up) DO_UP=0; shift ;;
    --no-build) BUILD=""; shift ;;
    --down) DOWN=1; shift ;;
    --timeout) TIMEOUT="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

for bin in docker curl python3; do
  command -v "$bin" >/dev/null 2>&1 || { echo "missing dependency: $bin" >&2; exit 2; }
done

DC=(docker compose --profile sim)
PASS=0
FAIL=0
RESULTS=()

ok()   { PASS=$((PASS + 1)); RESULTS+=("PASS  $1"); printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
ko()   { FAIL=$((FAIL + 1)); RESULTS+=("FAIL  $1${2:+ — $2}"); printf '  \033[31mFAIL\033[0m  %s%s\n' "$1" "${2:+ — $2}"; }
step() { printf '\n== %s\n' "$1"; }

if [ ! -f .env ]; then
  echo "No .env: copying .env.example (placeholders, demo only)."
  cp .env.example .env
fi

# Resolved configuration (interpolated by docker compose, so .env quoting rules apply).
CONFIG_JSON="$(mktemp)"
WORK="$(mktemp -d)"
trap 'rm -rf "$CONFIG_JSON" "$WORK"' EXIT
if ! "${DC[@]}" config --format json > "$CONFIG_JSON"; then
  echo "docker compose config failed" >&2
  exit 1
fi
cfg() { # cfg SERVICE VAR [DEFAULT]
  python3 - "$CONFIG_JSON" "$1" "$2" "${3:-}" <<'PY'
import json, sys
path, svc, var, default = sys.argv[1:5]
env = (json.load(open(path)).get("services", {}).get(svc, {}) or {}).get("environment") or {}
print(env.get(var) if env.get(var) is not None else default)
PY
}
FACTORY_HOST="$(cfg edge FACTORY_HOST localhost)"
FACTORY_SCHEME="$(cfg edge FACTORY_SCHEME http)"
HTTP_PORT="$(python3 - "$CONFIG_JSON" <<'PY'
import json, sys
ports = json.load(open(sys.argv[1]))["services"]["edge"].get("ports", [])
print(next((str(p.get("published")) for p in ports if str(p.get("target")) == "80" and p.get("protocol", "tcp") == "tcp"), "80"))
PY
)"
HTTPS_PORT="$(python3 - "$CONFIG_JSON" <<'PY'
import json, sys
ports = json.load(open(sys.argv[1]))["services"]["edge"].get("ports", [])
print(next((str(p.get("published")) for p in ports if str(p.get("target")) == "443" and p.get("protocol", "tcp") == "tcp"), "443"))
PY
)"
GRAFANA_USER="$(cfg grafana GF_SECURITY_ADMIN_USER admin)"
GRAFANA_PASS="$(cfg grafana GF_SECURITY_ADMIN_PASSWORD)"
LF_PK="$(cfg api LANGFUSE_PUBLIC_KEY)"
LF_SK="$(cfg api LANGFUSE_SECRET_KEY)"
if [ "$FACTORY_SCHEME" = "https" ]; then PORT="$HTTPS_PORT"; else PORT="$HTTP_PORT"; fi
BASE="$FACTORY_SCHEME://$FACTORY_HOST:$PORT"

# curl through the edge proxy on this host, whatever DNS says about the names.
ecurl() { # ecurl HOSTNAME PATH [curl args...]
  local host="$1" path="$2"; shift 2
  curl -sS --max-time 20 --resolve "$host:$PORT:127.0.0.1" "$@" "$FACTORY_SCHEME://$host:$PORT$path"
}
json_get() { # json_get FILE PYTHON_EXPR  (d = parsed JSON)
  python3 - "$1" "$2" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    v = eval(sys.argv[2], {"__builtins__": {"len": len, "any": any, "all": all, "set": set,
                                               "sorted": sorted, "str": str, "isinstance": isinstance,
                                               "list": list, "dict": dict}}, {"d": d})
except Exception as exc:  # noqa: BLE001
    print(f"ERR {exc.__class__.__name__}")
    sys.exit(0)
print(v)
PY
}

# ---------------------------------------------------------------------------- start
if [ "$DO_UP" = 1 ]; then
  step "docker compose --profile sim up -d $BUILD"
  # shellcheck disable=SC2086
  if ! "${DC[@]}" up -d $BUILD; then
    echo "docker compose up failed" >&2
    exit 1
  fi
fi

step "Waiting for the services (timeout ${TIMEOUT}s)"
deadline=$(( $(date +%s) + TIMEOUT ))
while :; do
  "${DC[@]}" ps -a --format json > "$WORK/ps.json" 2>/dev/null
  state="$(python3 - "$WORK/ps.json" <<'PY'
import json, sys
raw = open(sys.argv[1]).read().strip()
try:
    rows = json.loads(raw) if raw.startswith("[") else [json.loads(l) for l in raw.splitlines() if l.strip()]
except ValueError:
    rows = []
one_shot = {"grafana-bootstrap"}
pending, failed = [], []
for r in rows:
    svc, st, health, code = r.get("Service"), r.get("State"), r.get("Health") or "", r.get("ExitCode", 0)
    if svc in one_shot:
        if st == "exited" and code != 0:
            failed.append(f"{svc} (exit {code})")
        elif st != "exited":
            pending.append(svc)
    elif st != "running":
        (failed if st in ("exited", "dead") else pending).append(f"{svc} ({st})")
    elif health not in ("", "healthy"):
        pending.append(f"{svc} ({health})")
print("FAILED " + ", ".join(failed) if failed else ("PENDING " + ", ".join(pending) if pending else "READY"))
PY
)"
  case "$state" in
    READY) echo "  all services running and healthy"; break ;;
    FAILED*) echo "  $state"; break ;;
  esac
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "  timeout: $state"
    break
  fi
  sleep 5
done
if [ "$state" = "READY" ]; then ok "services healthy (profile sim)"; else ko "services healthy (profile sim)" "$state"; fi

# ------------------------------------------------------------------- edge and API
step "Edge proxy, dashboard and API ($BASE)"
if [ "$(ecurl "$FACTORY_HOST" /healthz)" = "ok" ]; then ok "edge /healthz"; else ko "edge /healthz"; fi

ecurl "$FACTORY_HOST" / -D "$WORK/index.headers" -o "$WORK/index.html"
if grep -q '"three": "/vendor/three/build/three.module.js"' "$WORK/index.html" \
   && [ "$(ecurl "$FACTORY_HOST" /vendor/three/build/three.module.js -o /dev/null -w '%{http_code}')" = "200" ] \
   && [ "$(ecurl "$FACTORY_HOST" /vendor/three/build/three.core.js -o /dev/null -w '%{http_code}')" = "200" ]; then
  ok "dashboard served with vendored Three.js (no CDN)"
else
  ko "dashboard served with vendored Three.js (no CDN)"
fi
csp="$(grep -i '^content-security-policy:' "$WORK/index.headers" | tr -d '\r')"
if [ -n "$csp" ] && ! grep -qi 'jsdelivr' <<<"$csp" && grep -qi '^x-frame-options: DENY' "$WORK/index.headers"; then
  ok "security headers (CSP without CDN, X-Frame-Options)"
else
  ko "security headers (CSP without CDN, X-Frame-Options)" "${csp:-no CSP}"
fi

ecurl "$FACTORY_HOST" /api/v1/health -o "$WORK/health.json"
if [ "$(json_get "$WORK/health.json" 'd["status"]')" = "ok" ]; then
  ok "api /api/v1/health (mode $(json_get "$WORK/health.json" 'd.get("mode")'))"
else
  ko "api /api/v1/health" "$(head -c 200 "$WORK/health.json")"
fi
if [ "$(ecurl "$FACTORY_HOST" /metrics -o /dev/null -w '%{http_code}')" != "200" ] \
   || ! ecurl "$FACTORY_HOST" /metrics | grep -q '^factory_'; then
  ok "API /metrics not exposed through the edge"
else
  ko "API /metrics not exposed through the edge"
fi

# Live data can take a few scrapes: retry the snapshot for up to 2 minutes.
snap_ok=""
for _ in $(seq 1 24); do
  ecurl "$FACTORY_HOST" /api/v1/snapshot -o "$WORK/snapshot.json"
  if [ "$(json_get "$WORK/snapshot.json" 'd["meta"]["mode"] != "demo" and d["meta"]["sources"]["prometheus"] and len(d["gpu"]["devices"]) > 0 and d["meta"].get("sections", {}).get("gpu", "live") == "live"')" = "True" ]; then
    snap_ok=1; break
  fi
  sleep 5
done
if [ -n "$snap_ok" ]; then
  ok "snapshot live (mode $(json_get "$WORK/snapshot.json" 'd["meta"]["mode"]'), GPU telemetry $(json_get "$WORK/snapshot.json" 'd["gpu"]["source"]'), $(json_get "$WORK/snapshot.json" 'len(d["gpu"]["devices"])') device(s))"
else
  ko "snapshot live with GPU telemetry" "$(json_get "$WORK/snapshot.json" 'd.get("meta")')"
fi
# Source health is cached by the API for a few seconds: retry while services settle.
for _ in $(seq 1 24); do
  ecurl "$FACTORY_HOST" /api/v1/health -o "$WORK/health2.json"
  sources="$(json_get "$WORK/health2.json" '",".join(k for k, v in sorted(d["sources"].items()) if not v)')"
  [ -z "$sources" ] && break
  sleep 5
done
if [ -z "$sources" ]; then ok "API: every live source reachable (prometheus, grafana, langfuse, litellm, qdrant)"; else ko "API: live sources" "unreachable: $sources"; fi

# -------------------------------------------------------------------- Prometheus
step "Prometheus"
prom() { "${DC[@]}" exec -T prometheus wget -qO- "http://127.0.0.1:9090$1"; }
targets_ok=""
for _ in $(seq 1 12); do
  prom '/api/v1/targets?state=active' > "$WORK/targets.json" 2>/dev/null
  down="$(json_get "$WORK/targets.json" '",".join(sorted(t["labels"]["job"] + "@" + t["labels"]["instance"] for t in d["data"]["activeTargets"] if t["health"] != "up"))')"
  jobs="$(json_get "$WORK/targets.json" '",".join(sorted(set(t["labels"]["job"] for t in d["data"]["activeTargets"])))')"
  if [ -z "$down" ] && grep -q 'gpu' <<<"$jobs" && grep -q 'litellm-callback' <<<"$jobs"; then targets_ok=1; break; fi
  sleep 5
done
if [ -n "$targets_ok" ]; then ok "Prometheus targets up ($jobs)"; else ko "Prometheus targets up" "down: ${down:-?} jobs: ${jobs:-?}"; fi

# -------------------------------------------------------------- LiteLLM gateway
step "LiteLLM gateway (through mock-llm)"
llm() { # llm MODEL METADATA_JSON -> prints "<http status> <x-litellm-model-id>"
  "${DC[@]}" exec -T litellm python3 - "$1" "$2" <<'PY'
import json, os, sys, urllib.request, urllib.error
model, metadata = sys.argv[1], json.loads(sys.argv[2])
body = {"model": model, "max_tokens": 16, "stream": False, "metadata": metadata,
        "messages": [{"role": "user", "content": "Smoke test (fictional data)."}]}
req = urllib.request.Request("http://127.0.0.1:4000/v1/chat/completions", data=json.dumps(body).encode(),
                             headers={"Authorization": "Bearer " + os.environ["LITELLM_MASTER_KEY"],
                                      "Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        print(r.status, r.headers.get("x-litellm-model-id", "?"))
except urllib.error.HTTPError as e:
    print(e.code, "error")
except Exception as e:  # noqa: BLE001
    print(0, type(e).__name__)
PY
}
mock_failure() { # mock_failure PROFILE RATE
  "${DC[@]}" exec -T mock-llm python -c "import json,sys,urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8080/admin/failure', data=json.dumps({'profile': sys.argv[1], 'rate': float(sys.argv[2])}).encode(), headers={'Content-Type': 'application/json'}), timeout=5)" "$1" "$2" >/dev/null
}
# Calls of the smoke test are attributed to a dedicated, non-registry use case label.
META='{"usecase":"smoke-test","agent":"ag-smoke-test","sensitivity":"C1"}'
# Pin the cloud failure rate to 0 for a deterministic first call.
mock_failure cloud 0
read -r st id <<<"$(llm chat-default "$META")"
if [ "$st" = "200" ] && [[ "$id" == cloud-* ]]; then ok "chat-default served by the cloud deployment ($id)"; else ko "chat-default (cloud)" "status $st id $id"; fi
read -r st id <<<"$(llm chat-sensitive "${META/C1/C3}")"
if [ "$st" = "200" ] && [[ "$id" == local-* ]]; then ok "chat-sensitive served by the local GPU ($id)"; else ko "chat-sensitive (local)" "status $st id $id"; fi
read -r st id <<<"$(llm chat-default "${META/C1/C4}")"
if [ "$st" = "200" ] && [ "$id" = "local-chat-sensitive" ]; then ok "C4 request on chat-default rerouted to the local GPU"; else ko "C4 reroute" "status $st id $id"; fi
mock_failure cloud 1
read -r st id <<<"$(llm chat-default "$META")"
if [ "$st" = "200" ] && [[ "$id" == vps-* ]]; then ok "cloud down: chat-default falls back to the VPS ($id)"; else ko "fallback cloud -> VPS" "status $st id $id"; fi
mock_failure local 1
read -r st id <<<"$(llm chat-sensitive "${META/C1/C3}")"
if [ "$st" != "200" ]; then ok "local GPU down: chat-sensitive fails closed (HTTP $st, no cloud/VPS fallback)"; else ko "chat-sensitive fail-closed" "served by $id"; fi
mock_failure cloud "$(cfg mock-llm MOCK_FAILURE_RATE_CLOUD 0.03)"
mock_failure local "$(cfg mock-llm MOCK_FAILURE_RATE_LOCAL 0.005)"

metrics_ok=""
for _ in $(seq 1 12); do
  prom '/api/v1/query?query=sum%20by%20(model)%20(factory_llm_requests_total)' > "$WORK/llm.json" 2>/dev/null
  models="$(json_get "$WORK/llm.json" '",".join(sorted(r["metric"].get("model", "") for r in d["data"]["result"]))')"
  if grep -q 'chat-default' <<<"$models" && grep -q 'chat-sensitive' <<<"$models"; then metrics_ok=1; break; fi
  sleep 5
done
if [ -n "$metrics_ok" ]; then ok "callback metrics in Prometheus (factory_llm_requests_total: $models)"; else ko "callback metrics in Prometheus" "${models:-none}"; fi
prom '/api/v1/query?query=count(factory_llm_ttft_seconds_bucket)%20and%20count(DCGM_FI_DEV_GPU_UTIL)' > "$WORK/ttft.json" 2>/dev/null
if [ "$(json_get "$WORK/ttft.json" 'len(d["data"]["result"]) > 0')" = "True" ]; then ok "TTFT histogram and DCGM metrics present"; else ko "TTFT histogram and DCGM metrics present"; fi

# ------------------------------------------------------------------------ Grafana
step "Grafana (grafana.$FACTORY_HOST, admin API)"
ecurl "grafana.$FACTORY_HOST" '/api/search?type=dash-db' -u "$GRAFANA_USER:$GRAFANA_PASS" -o "$WORK/dash.json"
want="agent-ops,factory-overview,finops,gpu-rack,llmops-pipeline,logs"
have="$(json_get "$WORK/dash.json" '",".join(sorted(x["uid"] for x in d))')"
if [ "$have" = "$want" ] || [ "$(python3 -c "import sys; print(set(sys.argv[1].split(',')) <= set(sys.argv[2].split(',')))" "$want" "$have")" = "True" ]; then
  ok "dashboards provisioned ($have)"
else
  ko "dashboards provisioned" "have: $have"
fi
for uid in prometheus loki; do
  ecurl "grafana.$FACTORY_HOST" "/api/datasources/uid/$uid/health" -u "$GRAFANA_USER:$GRAFANA_PASS" -o "$WORK/ds.json"
  if [ "$(json_get "$WORK/ds.json" 'd.get("status")')" = "OK" ]; then ok "datasource $uid healthy"; else ko "datasource $uid healthy" "$(head -c 200 "$WORK/ds.json")"; fi
done
if [ "$(ecurl "grafana.$FACTORY_HOST" /api/search -o /dev/null -w '%{http_code}')" = "401" ]; then
  ok "anonymous access refused (401)"
else
  ko "anonymous access refused"
fi
ecurl "$FACTORY_HOST" /api/v1/grafana/dashboards -o "$WORK/api-dash.json"
if grep -q 'factory-overview' "$WORK/api-dash.json"; then ok "API lists the dashboards through its server-side Viewer token"; else ko "API Grafana proxy" "$(head -c 200 "$WORK/api-dash.json")"; fi
token="$("${DC[@]}" exec -T api cat /var/run/factory/grafana/token 2>/dev/null || true)"
if [ -n "$token" ]; then
  leak=""
  for f in "$WORK/index.html" "$WORK/snapshot.json" "$WORK/health.json" "$WORK/api-dash.json"; do
    grep -qF "$token" "$f" && leak="$leak $(basename "$f")"
  done
  if [ -z "$leak" ]; then ok "Grafana token absent from every browser-facing response"; else ko "Grafana token leak" "$leak"; fi
else
  ko "Grafana token file written by grafana-bootstrap"
fi
unset token

# ----------------------------------------------------------------------- Langfuse
step "Langfuse (langfuse.$FACTORY_HOST)"
ecurl "langfuse.$FACTORY_HOST" /api/public/health -o "$WORK/lf.json"
if [ "$(json_get "$WORK/lf.json" 'd.get("status")')" = "OK" ]; then ok "Langfuse health"; else ko "Langfuse health" "$(head -c 200 "$WORK/lf.json")"; fi
traces_ok=""
for _ in $(seq 1 24); do
  ecurl "langfuse.$FACTORY_HOST" '/api/public/traces?limit=1' -u "$LF_PK:$LF_SK" -o "$WORK/traces.json"
  n="$(json_get "$WORK/traces.json" 'd["meta"]["totalItems"]')"
  if [[ "$n" =~ ^[0-9]+$ ]] && [ "$n" -gt 0 ]; then traces_ok=1; break; fi
  sleep 5
done
if [ -n "$traces_ok" ]; then ok "Langfuse receives traces ($n)"; else ko "Langfuse receives traces" "$(head -c 200 "$WORK/traces.json")"; fi
# Traces of the simulated C3/C4 use cases (tagged by the traffic generator) must be masked.
masked=""
for _ in $(seq 1 12); do
  ecurl "langfuse.$FACTORY_HOST" '/api/public/traces?limit=10&tags=C3' -u "$LF_PK:$LF_SK" -o "$WORK/sens.json"
  masked="$(json_get "$WORK/sens.json" 'len(d["data"]) > 0 and all(t.get("input") in (None, "redacted-by-litellm") and t.get("output") in (None, "redacted-by-litellm") for t in d["data"])')"
  [ "$masked" = "True" ] && break
  sleep 5
done
if [ "$masked" = "True" ]; then ok "sensitive (C3) traces masked in Langfuse"; else ko "sensitive (C3) traces masked in Langfuse" "$masked"; fi

# --------------------------------------------------------------------------- Loki
step "Loki (through the Grafana datasource proxy)"
ready=""
for _ in $(seq 1 12); do
  ready="$(ecurl "grafana.$FACTORY_HOST" /api/datasources/proxy/uid/loki/ready -u "$GRAFANA_USER:$GRAFANA_PASS" | tr -d '\r\n')"
  [ "$ready" = "ready" ] && break
  sleep 5
done
if [ "$ready" = "ready" ]; then ok "Loki ready"; else ko "Loki ready" "$ready"; fi
logs_ok=""
for _ in $(seq 1 12); do
  ecurl "grafana.$FACTORY_HOST" /api/datasources/proxy/uid/loki/loki/api/v1/label/service/values -u "$GRAFANA_USER:$GRAFANA_PASS" -o "$WORK/loki.json"
  svcs="$(json_get "$WORK/loki.json" '",".join(sorted(d.get("data") or []))')"
  if grep -q 'litellm' <<<"$svcs" && grep -q 'api' <<<"$svcs"; then logs_ok=1; break; fi
  sleep 5
done
if [ -n "$logs_ok" ]; then ok "container logs in Loki (services: $svcs)"; else ko "container logs in Loki" "${svcs:-none}"; fi

# ------------------------------------------------------------------------ summary
step "Summary"
for r in "${RESULTS[@]}"; do echo "  $r"; done
echo "  ---"
echo "  $PASS passed, $FAIL failed"

if [ "$DOWN" = 1 ]; then
  step "docker compose down (images and volumes kept)"
  "${DC[@]}" down
fi
[ "$FAIL" -eq 0 ]
