#!/usr/bin/env python3
"""Pull the organization-wide evidence rollup from Open Shadow AI."""

import argparse
from datetime import datetime, timedelta, timezone
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


MAX_RESPONSE_BYTES = 256 * 1024
MAX_PERIOD_BYTES = 64 * 1024
MAX_COUNT = 10**9
REQUEST_TIMEOUT = 10
HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.IGNORECASE | re.ASCII)
TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{1,8192}")
SESSION = re.compile(r"[A-Za-z0-9_.-]{1,128}=[A-Za-z0-9._~+/=%|-]{1,8192}")
UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", re.ASCII)
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
PERIOD = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")
PERIOD_FIELDS = {
    "team_usage": ("kind", "people", "events"),
    "adoption": ("active", "eligible"),
}


class BridgeError(Exception):
    """An error safe to report without including request or response data."""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Invalid arguments. Run with --help for usage.\n")


class NoRedirect(HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):
        raise HTTPError(req.full_url, code, msg, headers, fp)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def https_origin(value):
    error = BridgeError("An endpoint must be an HTTPS origin with no credentials, path, query or fragment.")
    if not value.isascii() or any(char.isspace() or ord(char) < 32 for char in value):
        raise error
    if not re.fullmatch(r"https://(?:\[[0-9a-fA-F:.]+\]|[A-Za-z0-9.-]+)(?::[0-9]{1,5})?/?", value):
        raise error
    try:
        parts = urlsplit(value)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise error from None
    if not host or len(host) > 253 or port == 0:
        raise error
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            raise error from None
    elif not all(HOST_LABEL.fullmatch(label) for label in host.split(".")):
        raise error
    return f"https://{parts.netloc}"


def _bearer(name, token):
    if not TOKEN.fullmatch(token):
        raise BridgeError(f"Set {name} to a valid short-lived bearer credential.")
    return {"Authorization": f"Bearer {token}"}


def _factory_auth():
    token = os.environ.get("SHADOW_AI_FACTORY_TOKEN", "")
    session = os.environ.get("SHADOW_AI_FACTORY_SESSION", "")
    if token and not session:
        return _bearer("SHADOW_AI_FACTORY_TOKEN", token)
    if session and not token and SESSION.fullmatch(session):
        return {"Cookie": session}
    raise BridgeError(
        "Set exactly one Factory credential: SHADOW_AI_FACTORY_TOKEN or SHADOW_AI_FACTORY_SESSION (name=value)."
    )


def _utc(value):
    if not isinstance(value, str) or not UTC_TIMESTAMP.fullmatch(value):
        raise ValueError
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def project_evidence(document, days):
    try:
        start, end = _utc(document["window_start"]), _utc(document["window_end"])
        events, users = document["total_events"], document["unique_users"]
        if end - start != timedelta(days=days):
            raise ValueError
        if type(events) is not int or type(users) is not int or not 0 <= users <= events <= MAX_COUNT:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise BridgeError("Open Shadow AI returned an invalid evidence rollup.") from None
    return {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "events": events,
        "unique_users": users,
    }


def _unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError
    return result


def read_period(path):
    try:
        with Path(path).open("rb") as stream:
            body = stream.read(MAX_PERIOD_BYTES + 1)
        if len(body) > MAX_PERIOD_BYTES:
            raise BridgeError("Monthly rollup file exceeded 64 KiB.")
        document = json.loads(body, object_pairs_hook=_unique_object)
    except (OSError, ValueError, RecursionError):
        raise BridgeError("Could not read the monthly rollup JSON.") from None
    if not (
        isinstance(document, dict)
        and document.keys() == {"period", *PERIOD_FIELDS}
        and isinstance(document["period"], str)
        and PERIOD.fullmatch(document["period"])
        and all(_period_rows(document[key], fields) for key, fields in PERIOD_FIELDS.items())
    ):
        raise BridgeError("Monthly rollup must contain only the documented aggregate fields.")
    return document


def _period_rows(rows, fields):
    if not isinstance(rows, list) or len(rows) > 200:
        return False
    for row in rows:
        if not isinstance(row, dict) or row.keys() != {"team", "tool", *fields}:
            return False
        if not all(isinstance(row[key], str) and len(row[key]) <= 64 and SLUG.fullmatch(row[key])
                   for key in ("team", "tool")):
            return False
        if "kind" in row and row["kind"] not in ("inventory", "observation", "usage"):
            return False
        if any(type(row[key]) is not int or not 0 <= row[key] <= MAX_COUNT for key in fields if key != "kind"):
            return False
    return True


def _request_json(opener, request, service):
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
            if response.headers.get_content_type() != "application/json":
                raise BridgeError(f"{service} did not return JSON.")
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        error.close()
        raise BridgeError(f"{service} rejected the request (HTTP {error.code}).") from None
    except (URLError, OSError, http.client.HTTPException):
        raise BridgeError(f"Could not reach {service} securely.") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise BridgeError(f"{service} response exceeded the size limit.")
    try:
        return json.loads(body, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        raise BridgeError(f"{service} returned invalid JSON.") from None


def _secure_opener():
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_default_certs()
    except OSError:
        raise BridgeError("Could not initialize the operator's TLS trust store.") from None
    return build_opener(HTTPSHandler(context=context), NoRedirect())


def _submit(opener, factory, factory_auth, dataset, payload):
    result = _request_json(
        opener,
        Request(
            f"{factory}/api/v1/shadow-ai/{dataset}",
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json", **factory_auth},
        ),
        "AI Factory",
    )
    if result != {"status": "stored"}:
        raise BridgeError("AI Factory did not confirm the import.")


def main(argv=None):
    parser = Parser(description=__doc__, allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--source", metavar="HTTPS_ORIGIN", help="Open Shadow AI origin")
    mode.add_argument("--period-file", metavar="FILE", help="private company monthly rollup JSON")
    parser.add_argument("--factory", metavar="HTTPS_ORIGIN", help="Factory SSO edge origin (omit for JSON on stdout)")
    parser.add_argument("--days", type=int, choices=range(1, 91), metavar="1..90", help="source window (default 30)")
    args = parser.parse_args(argv)
    if args.period_file is not None and (args.factory is None or args.days is not None):
        parser.error("Monthly mode requires a Factory origin and no source window.")
    try:
        factory = https_origin(args.factory) if args.factory is not None else None
        factory_auth = _factory_auth() if factory else {}
        if args.period_file is not None:
            payload = read_period(args.period_file)
            _submit(_secure_opener(), factory, factory_auth, "periods", payload)
            return 0
        source = https_origin(args.source)
        source_auth = _bearer("SHADOW_AI_SOURCE_TOKEN", os.environ.get("SHADOW_AI_SOURCE_TOKEN", ""))
        days = args.days if args.days is not None else 30
        opener = _secure_opener()
        evidence = _request_json(
            opener,
            Request(
                f"{source}/api/v1/dashboard/evidence?days={days}",
                headers={"Accept": "application/json", **source_auth},
            ),
            "Open Shadow AI",
        )
        payload = project_evidence(evidence, days)
        if factory:
            _submit(opener, factory, factory_auth, "evidence", payload)
        else:
            print(json.dumps(payload))
    except BridgeError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
