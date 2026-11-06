# SPDX-License-Identifier: MIT
"""Client of compose/caddy/tests/test_identity_headers.sh: sends forged identity headers to every
route of the edge (dashboard, API, OpenAPI page, Grafana, Langfuse, alternate ports, oauth2-proxy)
and checks, from what the upstream stand-in echoes, that only the edge itself sets them.
Also checks the Content-Security-Policy of each route (dashboard policy never on /api/*).

Environment: EDGE_IP, MODE (none | basic | sso), BASIC_USER and BASIC_PASSWORD (basic mode).
Prints one PASS/FAIL line per check; exit code 1 when a check fails.
"""
import base64
import http.client
import json
import os
import sys

EDGE_IP = os.environ["EDGE_IP"]
MODE = os.environ["MODE"]
HOST = "factory.test"
IDENTITY = ("x-forwarded-user", "x-forwarded-email", "x-forwarded-groups", "x-forwarded-preferred-username",
            "x-auth-request-user", "x-auth-request-email", "x-auth-request-groups",
            "x-auth-request-preferred-username")
# Forged by the client, in two spellings and twice for the user, plus a forged client address.
FORGED = [("X-Forwarded-User", "forged-admin"), ("x-forwarded-user", "forged-admin-2"),
          ("X-Forwarded-Email", "forged@attacker.example"), ("X-Forwarded-Groups", "admins,ai-governance"),
          ("X-Forwarded-Preferred-Username", "forged"), ("X-Auth-Request-User", "forged-admin"),
          ("X-Auth-Request-Email", "forged@attacker.example"), ("X-Auth-Request-Groups", "admins"),
          ("X-Forwarded-For", "203.0.113.66"), ("X-Real-IP", "203.0.113.66")]
API_DEFAULT_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
UPSTREAM_DOCS_CSP = "default-src 'none'; script-src 'nonce-upstream-test' https://cdn.jsdelivr.net"
failures = 0
checks = 0


def check(name: str, ok: bool, detail: object = "") -> None:
    global failures, checks
    checks += 1
    failures += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  [{MODE}] {name}{'' if ok else ' -- ' + str(detail)}")


def request(host: str, port: int, path: str, extra: list[tuple[str, str]] | None = None):
    conn = http.client.HTTPConnection(EDGE_IP, port, timeout=20)
    conn.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
    conn.putheader("Host", host)
    for name, value in FORGED + (extra or []):
        conn.putheader(name, value)
    conn.endheaders()
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    try:
        echo = json.loads(body)
    except ValueError:
        echo = None
    return resp.status, resp.headers, echo


def identity_seen(echo: dict) -> dict[str, list[str]]:
    return {k: v for k, v in echo["headers"].items() if k in IDENTITY}


def expect_identity(label: str, echo: dict | None, want: dict[str, str], upstream_port: int) -> None:
    if not isinstance(echo, dict):
        check(f"{label}: request reaches the upstream", False, "no echo")
        return
    seen = identity_seen(echo)
    check(f"{label}: identity headers seen by the upstream are only the edge's {sorted(want) or '(none)'}",
          seen == {k: [v] for k, v in want.items()} and echo["port"] == upstream_port, seen)
    xff = echo["headers"].get("x-forwarded-for", [""])
    real = echo["headers"].get("x-real-ip", [""])
    check(f"{label}: forged client address dropped (X-Forwarded-For, X-Real-IP)",
          not any("203.0.113.66" in v for v in xff + real), {"x-forwarded-for": xff, "x-real-ip": real})


auth: list[tuple[str, str]] = []
if MODE == "basic":
    token = base64.b64encode(f"{os.environ['BASIC_USER']}:{os.environ['BASIC_PASSWORD']}".encode()).decode()
    auth = [("Authorization", f"Basic {token}")]
    app_identity = {"x-forwarded-user": os.environ["BASIC_USER"]}
elif MODE == "sso":
    auth = [("Cookie", "idh=user")]
    app_identity = {"x-forwarded-user": "sso-user", "x-forwarded-email": "sso-user@example.test",
                    "x-forwarded-groups": "ai-governance"}
else:
    app_identity = {}

# -------------------------------------------------------------- unauthenticated requests
if MODE == "basic":
    status, _, echo = request(HOST, 80, "/api/v1/whoami")
    check("API without credentials refused at the edge (401, upstream not reached)", status == 401 and echo is None, status)
    status, _, echo = request(HOST, 80, "/")
    check("dashboard without credentials refused at the edge (401)", status == 401 and echo is None, status)
if MODE == "sso":
    status, _, echo = request(HOST, 80, "/api/v1/whoami")
    # The 401 body is oauth2-proxy's answer to forward_auth (port 4180), never the API's.
    check("API without session refused at the edge (401 from forward_auth, API not reached)",
          status == 401 and (echo is None or echo.get("port") == 4180), (status, echo and echo.get("port")))
    status, headers, echo = request(HOST, 80, "/")
    check("page without session redirected to sign-in",
          status == 302 and (headers.get("Location") or "").startswith("/oauth2/sign_in"), status)
    status, _, echo = request(HOST, 80, "/oauth2/start?rd=/")
    expect_identity("/oauth2/* (oauth2-proxy, no forward_auth)", echo, {}, 4180)
    # A session without e-mail: the forged X-Forwarded-Email must not survive (copy_headers
    # deletes the client copies of every header it manages, even those the answer lacks).
    status, _, echo = request(HOST, 80, "/api/v1/whoami", [("Cookie", "idh=nomail")])
    expect_identity("API, session without e-mail or groups", echo, {"x-forwarded-user": "sso-nomail"}, 8000)

# -------------------------------------------------------------- authenticated requests
status, headers, echo = request(HOST, 80, "/api/v1/whoami", auth)
expect_identity("API /api/v1/whoami", echo, app_identity, 8000)
if isinstance(echo, dict):
    check("API sees the fixed edge address as TCP peer", echo["peer"] == EDGE_IP, echo["peer"])
csp = headers.get_all("Content-Security-Policy") or []
check("API JSON: one CSP, the API default (no dashboard policy)", csp == [API_DEFAULT_CSP], csp)

status, headers, echo = request(HOST, 80, "/api/v1/docs", auth)
expect_identity("API /api/v1/docs", echo, app_identity, 8000)
csp = headers.get_all("Content-Security-Policy") or []
check("OpenAPI page: the API's own CSP only (nonce + CDN), untouched by the edge", csp == [UPSTREAM_DOCS_CSP], csp)

status, headers, echo = request(HOST, 80, "/", auth)
expect_identity("dashboard /", echo, app_identity, 80)
csp = headers.get_all("Content-Security-Policy") or []
check("dashboard: one CSP, the dashboard policy", len(csp) == 1 and "script-src 'self'" in csp[0], csp)

for label, host, port in (("Grafana", f"grafana.{HOST}", 80), ("Langfuse", f"langfuse.{HOST}", 80),
                          ("Grafana alternate port 3001", "localhost", 3001),
                          ("Langfuse alternate port 3002", "localhost", 3002)):
    status, headers, echo = request(host, port, "/", auth)
    if os.environ.get("FACTORY_PRIVATE_TELEMETRY") == "disabled":
        check(f"{label}: private telemetry blocked at the edge", status == 404 and echo is None, status)
    else:
        expect_identity(label, echo, {}, 3000)

status, _, echo = request(HOST, 80, "/metrics", auth)
check("internal Prometheus endpoint blocked at the edge", status == 404 and echo is None, status)

for path in ("/healthz", "/.well-known/security.txt"):
    status, _, echo = request(HOST, 80, path)
    check(f"{path} answered by the edge itself, outside authentication", status == 200 and echo is None, status)

print(f"  [{MODE}] {checks - failures}/{checks} checks passed")
sys.exit(1 if failures else 0)
