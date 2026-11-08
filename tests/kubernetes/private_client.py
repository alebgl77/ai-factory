# SPDX-License-Identifier: MIT
"""Cookie-jar browser stand-in, as in the Compose SSO test, with strict TLS and two BUs."""

import http.cookiejar
import json
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://factory.test"
DEX = "dex.kube-test.svc.cluster.local"
SPOOF = {
    "X-Forwarded-User": "forged",
    "X-Forwarded-Email": "forged@factory.test",
    "X-Forwarded-Groups": "admins,ops,finance",
    "X-Auth-Request-User": "forged",
    "X-Auth-Request-Email": "forged@factory.test",
    "X-Auth-Request-Groups": "admins,ops,finance",
    "X-Forwarded-Proto": "http",
    "X-Forwarded-Host": "attacker.test",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class SyntheticIdentity(urllib.request.BaseHandler):
    def __init__(self, name, group):
        self.name = name
        self.group = group

    def https_request(self, request):
        url = urllib.parse.urlsplit(request.full_url)
        if url.hostname == DEX and url.path == "/dex/callback/synthetic":
            email = f"{self.name}@factory.test"
            request.add_unredirected_header("X-Remote-User", email)
            request.add_unredirected_header("X-Remote-User-Id", self.name)
            request.add_unredirected_header("X-Remote-User-Email", email)
            request.add_unredirected_header("X-Remote-Group", self.group)
        return request


def browser(context, handler):
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), handler,
    )


def request(opener, path, status, *, headers=None, payload=None, origin=BASE):
    request_headers = dict(headers or {})
    if payload is not None:
        request_headers.update({"Content-Type": "application/json", "Origin": origin})
    req = urllib.request.Request(
        BASE + path, headers=request_headers,
        data=json.dumps(payload).encode() if payload is not None else None,
    )
    try:
        response = opener.open(req, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        assert response.code == status, (path, response.code, status)
        return response.headers, response.read(), response.geturl()


def main(factory_cert, dex_cert):
    context = ssl.create_default_context(cafile=factory_cert)
    context.load_verify_locations(cafile=dex_cert)
    getaddrinfo = socket.getaddrinfo

    def forward(host, port, *args, **kwargs):
        # Resolve only the fixture endpoints to the local port-forwards. Preserve Host and SNI.
        targets = {("factory.test", 443): 18443, (DEX, 5556): 15556}
        return getaddrinfo("127.0.0.1", targets[(host, int(port))], *args, **kwargs)

    socket.getaddrinfo = forward
    anonymous = browser(context, NoRedirect())
    headers, _, _ = request(anonymous, "/", 302, headers=SPOOF)
    location = urllib.parse.urlsplit(headers["Location"])
    assert location.path == "/oauth2/sign_in" and location.hostname in (None, "factory.test")
    assert urllib.parse.parse_qs(location.query)["rd"] == [BASE + "/"]
    request(anonymous, "/api/v1/registry", 401, headers=SPOOF)
    request(anonymous, "/api/v1/whoami", 401)
    request(anonymous, "/healthz", 200)
    print("PASS anonymous redirect, same-origin return and forged identity denied")

    ops = browser(context, SyntheticIdentity("alice", "ops"))
    finance = browser(context, SyntheticIdentity("carol", "finance"))
    _, page, landing = request(ops, "/", 200)
    assert landing == BASE + "/" and b"/vendor/three/build/three.module.js" in page
    request(finance, "/", 200)
    outsider = browser(context, SyntheticIdentity("eve", "outside"))
    _, _, callback = request(outsider, "/", 403)
    assert urllib.parse.urlsplit(callback).path == "/oauth2/callback"
    request(outsider, "/api/v1/registry", 401, headers=SPOOF)
    print("PASS two Dex sign-ins and an identity rejected by the oauth2-proxy group gate")

    for opener, name, center, own, foreign in (
        (ops, "alice", "cc-ops", "uc-01", "uc-05"),
        (finance, "carol", "cc-finance", "uc-05", "uc-01"),
    ):
        _, raw, _ = request(opener, "/api/v1/whoami", 200, headers=SPOOF)
        identity = json.loads(raw)
        assert identity["authenticated"] and identity["actor"] == f"{name}@factory.test"
        _, raw, _ = request(opener, "/api/v1/snapshot", 200, headers=SPOOF)
        assert {row["cost_center"] for row in json.loads(raw)["registry"]} == {center}
        request(opener, f"/api/v1/registry/{foreign}", 404, headers=SPOOF)
        request(opener, f"/api/v1/registry/{foreign}/model-version", 404,
                headers=SPOOF, payload={"version": "kind-foreign"})
        request(opener, f"/api/v1/registry/{own}/model-version", 403,
                payload={"version": "kind-invalid-origin"}, origin="https://attacker.test")
        request(opener, f"/api/v1/registry/{own}/model-version", 200,
                headers=SPOOF, payload={"version": f"kind-own-{name}"})
        request(opener, "/api/v1/audit", 403, headers=SPOOF)
    print("PASS authentic provider identities, forged privilege denied, cross-BU read and write denied")


if __name__ == "__main__":
    main(*sys.argv[1:])
