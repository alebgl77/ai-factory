# SPDX-License-Identifier: MIT
"""Stand-in for the dashboard (port 80) and the API (port 8000): echoes the request headers and
the TCP peer address as JSON, so the test sees exactly what the edge forwards."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Echo(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"peer": self.client_address[0], "port": self.server.server_port, "path": self.path,
                           "headers": {k.lower(): v for k, v in self.headers.items()}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


for port in (80, 8000):
    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", port), Echo).serve_forever, daemon=True).start()
threading.Event().wait()
