# SPDX-License-Identifier: MIT
"""Stand-in for every upstream of the edge in compose/caddy/tests/test_identity_headers.sh.

Ports: 80 dashboard, 8000 API, 3000 Grafana and Langfuse, 4180 oauth2-proxy. Every answer echoes
the request as JSON (TCP peer, port, path, every header value, duplicates kept), so the test sees
exactly what the edge forwards. Port 4180 also answers Caddy's forward_auth on /oauth2/auth like
oauth2-proxy does: 202 with X-Auth-Request-* headers for a test session cookie, 401 otherwise.
The API stand-in sends its own Content-Security-Policy on /api/v1/docs only, so the test can
tell the API's policy from the edge default and from the dashboard policy.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_DOCS_CSP = "default-src 'none'; script-src 'nonce-upstream-test' https://cdn.jsdelivr.net"
SESSIONS = {
    "idh=user": {"X-Auth-Request-User": "sso-user", "X-Auth-Request-Email": "sso-user@example.test",
                 "X-Auth-Request-Groups": "ai-governance"},
    "idh=nomail": {"X-Auth-Request-User": "sso-nomail"},
}


class Echo(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _answer(self) -> None:
        port = self.server.server_port
        extra: dict[str, str] = {}
        status = 200
        if port == 4180 and self.path.startswith("/oauth2/auth"):
            session = SESSIONS.get((self.headers.get("Cookie") or "").strip())
            if session is None:
                status = 401
            else:
                status, extra = 202, session
        if port == 8000 and self.path.startswith("/api/v1/docs"):
            extra["Content-Security-Policy"] = API_DOCS_CSP
        headers: dict[str, list[str]] = {}
        for name in self.headers.keys():
            headers.setdefault(name.lower(), self.headers.get_all(name) or [])
        body = json.dumps({"peer": self.client_address[0], "port": port, "path": self.path,
                           "headers": headers}).encode()
        self.send_response(status)
        for name, value in extra.items():
            self.send_header(name, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = _answer  # noqa: N815

    def log_message(self, *args) -> None:
        pass


for listen_port in (80, 8000, 3000, 4180):
    server = ThreadingHTTPServer(("0.0.0.0", listen_port), Echo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
threading.Event().wait()
