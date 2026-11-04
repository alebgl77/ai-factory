# SPDX-License-Identifier: MIT
"""Browser stand-in for compose/sso/test_sso_e2e.sh: anonymous access, spoofing attempts and a
full OpenID Connect sign-in through the edge, oauth2-proxy and the test identity provider.
Prints one PASS/FAIL line per check; exit code 1 when a check fails."""
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "http://factory.test"
EDGE_IP = os.environ["EDGE_IP"]
EXPECT = os.environ.get("EXPECT", "allowed")  # allowed | denied (user outside SSO_ALLOWED_GROUPS)
failures = 0


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def browser(follow=True):
    handlers = [urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())]
    if not follow:
        handlers.append(NoRedirect())
    return urllib.request.build_opener(*handlers)


def get(opener, path, headers=None):
    try:
        with opener.open(urllib.request.Request(BASE + path, headers=headers or {}), timeout=20) as r:
            return r.status, r.headers, r.read(), r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read(), e.geturl()


def check(name, ok, detail=""):
    global failures
    failures += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'' if ok else ' -- ' + str(detail)}")


SPOOF = {"X-Forwarded-User": "mallory", "X-Forwarded-Email": "mallory@attacker.example",
         "X-Forwarded-Groups": "ai-governance,admins"}
anon = browser(follow=False)
s, h, _, _ = get(anon, "/")
check("anonymous page redirected to sign-in", s == 302 and h.get("Location", "").startswith("/oauth2/sign_in"), s)
s, *_ = get(anon, "/api/v1/registry")
check("anonymous API call refused (401, no redirect)", s == 401, s)
s, *_ = get(anon, "/api/v1/registry", SPOOF)
check("anonymous API call with forged identity headers refused", s == 401, s)
s, _, b, _ = get(anon, "/.well-known/security.txt")
check("security.txt public", s == 200 and b"Contact:" in b, s)
s, _, b, _ = get(anon, "/healthz")
check("edge /healthz public", s == 200 and b == b"ok", s)

user = browser()
s, _, b, url = get(user, "/")
if EXPECT == "denied":
    check("user outside the allowed groups refused at the callback (403)", s == 403 and "/oauth2/callback" in url, s)
    s, *_ = get(user, "/api/v1/registry")
    check("refused user gets no API access", s == 401, s)
else:
    check("OIDC sign-in lands on the dashboard", s == 200 and json.loads(b).get("port") == 80, (s, b[:120]))
    s, _, b, _ = get(user, "/api/v1/whoami", SPOOF)
    seen = json.loads(b) if s == 200 else {}
    hd = seen.get("headers", {})
    check("API receives the identity of the provider",
          s == 200 and hd.get("x-forwarded-email") == "kilgore@kilgore.trout"
          and hd.get("x-forwarded-groups") == "authors" and bool(hd.get("x-forwarded-user")), hd)
    check("forged identity headers never reach the API",
          not any(v in json.dumps(hd) for v in ("mallory", "attacker.example", "ai-governance", "admins")), hd)
    check("identity asserted from the fixed edge address only", seen.get("peer") == EDGE_IP, seen.get("peer"))
    check("no X-Auth-Request-* header leaks to the API", not [k for k in hd if k.startswith("x-auth-request")], hd)

sys.exit(1 if failures else 0)
