# SPDX-License-Identifier: MIT
"""One-shot Grafana bootstrap of the Virtual AI Factory (standard library only).

Runs at every `docker compose up` and is idempotent:

1. waits for Grafana (GRAFANA_URL/api/health, database "ok");
2. with the admin credentials, creates the service account GRAFANA_SA_NAME with the
   Viewer role when it does not exist (and re-asserts Viewer when it does);
3. keeps a valid token in GRAFANA_TOKEN_FILE, a file on a dedicated volume that only the
   aggregation API mounts (read-only): an existing token still accepted by Grafana is
   kept; otherwise the stale tokens of the service account are revoked and a new one is
   written atomically.

The token is never printed and never reaches the browser.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

URL = os.environ.get("GRAFANA_URL", "http://grafana:3000").rstrip("/")
SA_NAME = os.environ.get("GRAFANA_SA_NAME", "factory-api-viewer")
TOKEN_FILE = os.environ.get("GRAFANA_TOKEN_FILE", "/var/run/factory/grafana/token")
WAIT_S = int(os.environ.get("GRAFANA_BOOTSTRAP_WAIT_SECONDS", "180"))
USER = os.environ.get("GRAFANA_ADMIN_USER", "")
PASSWORD = os.environ.get("GRAFANA_ADMIN_PASSWORD", "")


def log(level: str, msg: str, **fields: Any) -> None:
    print(json.dumps({"level": level, "msg": msg, **fields}), flush=True)


def request(method: str, path: str, body: Any = None, auth: str = "admin", token: str | None = None,
            timeout: float = 10.0) -> tuple[int, Any]:
    headers = {"Accept": "application/json"}
    if auth == "admin":
        cred = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        headers["Authorization"] = f"Basic {cred}"
    elif auth == "token" and token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(URL + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw) if raw else None
        except ValueError:
            return exc.code, None


def must(status: int, payload: Any, what: str) -> Any:
    if status >= 300:
        message = payload.get("message") if isinstance(payload, dict) else None
        log("error", f"{what} failed", status=status, detail=message)
        sys.exit(1)
    return payload


def wait_for_grafana() -> None:
    deadline = time.monotonic() + WAIT_S
    while True:
        try:
            status, payload = request("GET", "/api/health", auth="none", timeout=5)
            if status == 200 and isinstance(payload, dict) and payload.get("database") == "ok":
                return
        except (urllib.error.URLError, OSError, ValueError):
            pass
        if time.monotonic() > deadline:
            log("error", "grafana not ready", waited_s=WAIT_S)
            sys.exit(1)
        time.sleep(3)


def existing_token_valid() -> bool:
    try:
        with open(TOKEN_FILE, encoding="utf-8") as fh:
            token = fh.read().strip()
    except OSError:
        return False
    if not token:
        return False
    status, _ = request("GET", "/api/search?limit=1", auth="token", token=token)
    if status == 200:
        return True
    log("warn", "existing token rejected, issuing a new one", status=status)
    return False


def write_token(token: str) -> None:
    directory = os.path.dirname(TOKEN_FILE) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, f".token.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token)
    # Readable by the API container (another uid) through this dedicated volume only.
    os.chmod(tmp, 0o644)
    os.replace(tmp, TOKEN_FILE)


def main() -> None:
    if not USER or not PASSWORD:
        log("error", "GRAFANA_ADMIN_USER and GRAFANA_ADMIN_PASSWORD are required")
        sys.exit(1)
    log("info", "waiting for grafana", url=URL)
    wait_for_grafana()
    if existing_token_valid():
        log("info", "existing viewer token is valid, nothing to do")
        return

    query = urllib.parse.urlencode({"perpage": 50, "page": 1, "query": SA_NAME})
    found = must(*request("GET", f"/api/serviceaccounts/search?{query}"), "service account search")
    matches = [sa for sa in (found or {}).get("serviceAccounts", []) if sa.get("name") == SA_NAME]
    if matches:
        sa_id = matches[0]["id"]
        # Enforce the least-privilege role even if it was changed in the UI.
        must(*request("PATCH", f"/api/serviceaccounts/{sa_id}", {"role": "Viewer", "isDisabled": False}),
             "service account update")
        log("info", "service account found", name=SA_NAME, role="Viewer")
    else:
        created = must(*request("POST", "/api/serviceaccounts",
                                {"name": SA_NAME, "role": "Viewer", "isDisabled": False}),
                       "service account creation")
        sa_id = created["id"]
        log("info", "service account created", name=SA_NAME, role="Viewer")

    tokens = must(*request("GET", f"/api/serviceaccounts/{sa_id}/tokens"), "token listing") or []
    for tok in tokens:
        request("DELETE", f"/api/serviceaccounts/{sa_id}/tokens/{tok['id']}")
    name = "factory-api-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    issued = must(*request("POST", f"/api/serviceaccounts/{sa_id}/tokens", {"name": name, "secondsToLive": 0}),
                  "token creation")
    key = (issued or {}).get("key")
    if not key:
        log("error", "token creation returned no key")
        sys.exit(1)
    write_token(key)
    log("info", "viewer token written to the shared volume", revoked=len(tokens))


if __name__ == "__main__":
    main()
