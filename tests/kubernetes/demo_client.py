# SPDX-License-Identifier: MIT
"""Check the dashboard origin, durable demo state, and the reconciled audit trail."""

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:18080"
STATE = "/api/v1/spaces/state"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as response:
        return response.read()


def audit():
    result = json.loads(get("/api/v1/audit/verify"))
    assert result["ok"] and result["records"] >= 1, result
    return result["head_hash"]


def main(phase, evidence_file):
    assert get("/healthz") == b"ok"
    assert b'"/vendor/three/build/three.module.js"' in get("/")
    assert len(get("/vendor/three/build/three.module.js")) > 1000
    health = json.loads(get("/api/v1/health"))
    snapshot = json.loads(get("/api/v1/snapshot"))
    assert health["status"] == "ok" and health["mode"] == "demo", health
    assert snapshot["meta"]["mode"] == "demo" and snapshot["registry"]
    if phase == "write":
        current = json.loads(get(STATE))
        payload = {"version": current["version"], "state": {"kubeTest": "retained", "bookings": []}}
        req = urllib.request.Request(
            BASE + STATE, method="PUT", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Origin": BASE},
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            saved = json.load(response)
        assert saved["version"] == current["version"] + 1 and saved["state"] == payload["state"], saved
        with open(evidence_file, "w", encoding="utf-8") as output:
            json.dump({"version": saved["version"], "state": saved["state"], "audit": audit()}, output)
    else:
        with open(evidence_file, encoding="utf-8") as source:
            expected = json.load(source)
        current = json.loads(get(STATE))
        assert current["version"] == expected["version"] and current["state"] == expected["state"], current
        assert audit() == expected["audit"]
    print(f"PASS demo {phase}: dashboard, health, snapshot, state and audit")


if __name__ == "__main__":
    main(*sys.argv[1:])
