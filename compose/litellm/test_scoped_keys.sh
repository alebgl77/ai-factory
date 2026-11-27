#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Disposable credential checks against the simulated gateway, with cleanup on failure.
set -euo pipefail
if [ "$#" -eq 0 ]; then set -- docker compose --profile sim; fi
"$@" exec -T litellm python3 - <<'PY'
import json
import datetime as dt
import hashlib
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

if os.environ.get("FACTORY_DEMO_MODE", "auto") not in {"auto", "always"}:
    raise SystemExit("Scoped credential checks require the simulated gateway.")

admin = os.environ["LITELLM_MASTER_KEY"]
prefix = "scope-test-" + uuid.uuid4().hex
users = []
keys = []
failures = []


def request(path, key, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request("http://127.0.0.1:4000" + path, data=data, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read(1048576)
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        return error.code, {}


def require(label, status, expected):
    if status not in expected:
        failures.append(label)


def scoped_key(role, routes, models):
    user = prefix + "-" + str(len(users))
    # Track the identity before dispatch so a partially successful response can still be cleaned up.
    users.append(user)
    status, _ = request("/user/new", admin, {"user_id": user, "user_role": role, "auto_create_key": False})
    if status != 200:
        raise RuntimeError("Temporary gateway identity creation failed")
    status, body = request("/key/generate", admin, {
        "user_id": user, "key_alias": user, "allowed_routes": routes,
        "models": models, "duration": "10m", "rpm_limit": 10, "max_parallel_requests": 1,
    })
    key = body.get("key")
    if status != 200 or not isinstance(key, str) or key == admin:
        raise RuntimeError("Temporary scoped gateway credential creation failed")
    keys.append(key)
    return key


try:
    telemetry = scoped_key("proxy_admin_viewer", ["/model/info", "/spend/logs"], [])
    inference = scoped_key("internal_user", ["/v1/chat/completions"], ["chat-sensitive"])
    require("telemetry model read", request("/model/info", telemetry)[0], {200})
    require("telemetry spend read", request("/spend/logs", telemetry)[0], {200})
    for name, key in (("telemetry", telemetry), ("inference", inference)):
        for path in ("/key/generate", "/config/update"):
            require(name + " administrative denial", request(path, key, {})[0], {401, 403})
    prompt = {"model": "chat-sensitive", "max_tokens": 4,
              "messages": [{"role": "user", "content": "Synthetic scope check."}],
              "metadata": {"usecase": "smoke-test", "sensitivity": "C3"}}
    require("telemetry inference denial", request("/v1/chat/completions", telemetry, prompt)[0], {401, 403})
    require("local scoped inference", request("/v1/chat/completions", inference, prompt)[0], {200})
    today = dt.datetime.now(dt.timezone.utc).date()
    query = ("/spend/logs?start_date=" + today.isoformat()
             + "&end_date=" + (today + dt.timedelta(days=1)).isoformat()
             + "&summarize=false&api_key=" + hashlib.sha256(inference.encode()).hexdigest())
    raw_spend_verified = False
    for _ in range(20):
        status, rows = request(query, telemetry)
        if status == 200 and isinstance(rows, list) and rows:
            raw_spend_verified = all(
                isinstance(row, dict) and isinstance(row.get("startTime"), str)
                and type(row.get("prompt_tokens")) is int and type(row.get("completion_tokens")) is int
                and isinstance(row.get("metadata"), dict)
                and (row["metadata"].get("usecase")
                     or (row["metadata"].get("requester_metadata") or {}).get("usecase")) == "smoke-test"
                for row in rows
            )
            break
        time.sleep(3)
    if not raw_spend_verified:
        failures.append("raw attributed spend shape")
    prompt["model"] = "chat-default"
    require("cloud model denial", request("/v1/chat/completions", inference, prompt)[0], {401, 403})
    require("inference telemetry denial", request("/spend/logs", inference)[0], {401, 403})
except Exception:
    failures.append("scoped credential exercise")
finally:
    if keys:
        try:
            require("temporary key cleanup", request("/key/delete", admin, {"keys": keys})[0], {200})
        except Exception:
            failures.append("temporary key cleanup")
    if users:
        try:
            require("temporary identity cleanup", request("/user/delete", admin, {"user_ids": users})[0], {200})
        except Exception:
            failures.append("temporary identity cleanup")

if failures:
    print("Scoped gateway credential checks failed: " + ", ".join(sorted(set(failures))), file=sys.stderr)
    raise SystemExit(1)
print("Scoped gateway credential checks passed.")
PY
