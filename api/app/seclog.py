"""Structured JSON log lines for security and audit events (one JSON object per line).

The lines go to stderr, which the container runtime collects (Alloy ships them to Loki), so the
security team can alert on them without parsing free text. They never carry a secret, a request
body, a query string or an identity header value sent by an untrusted source.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import sys
from collections.abc import Mapping
from typing import Any

SECURITY_LOGGER = "factory.security"
AUDIT_LOGGER = "factory.audit"
API_LOGGER = "factory.api"
HTTP_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT", "TRACE"})


def reference(kind: str, value: str | None) -> str | None:
    """Correlate operational events without emitting the original identifier."""
    if value is None:
        return None
    data = json.dumps(["factory-operational-ref-v1", kind, value], ensure_ascii=True).encode("ascii")
    return hashlib.sha256(data).hexdigest()


def route_label(scope: Mapping[str, Any]) -> str:
    """Read the application-defined route template, never the incoming path."""
    template = getattr(scope.get("route"), "path", None)
    return template if isinstance(template, str) else "unresolved"


def _scrub_asgi_exception(record: logging.LogRecord) -> bool:
    # Starlette re-raises after the generic HTTP 500, causing Uvicorn to log the exception again.
    if record.msg == "Exception in ASGI application\n":
        record.msg = "Exception in ASGI application"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
    return True


def protect_uvicorn_errors() -> None:
    logging.getLogger("uvicorn.error").addFilter(_scrub_asgi_exception)


class _StderrHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Writes to the *current* ``sys.stderr`` (so test capture and stream swaps keep working)."""

    def __init__(self) -> None:
        super().__init__(sys.stderr)

    @property  # type: ignore[override]
    def stream(self) -> Any:
        return sys.stderr

    @stream.setter
    def stream(self, _value: Any) -> None:
        pass


def _logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if not any(isinstance(h, _StderrHandler) for h in log.handlers):
        handler = _StderrHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False  # one line per event, never duplicated by a root handler
    return log


def emit(logger_name: str, event: str, level: int = logging.WARNING, **fields: Any) -> dict[str, Any]:
    """Log one JSON line ``{ts, level, logger, event, ...fields}``; ``None`` fields are dropped."""
    payload: dict[str, Any] = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "level": logging.getLevelName(level).lower(),
        "logger": logger_name,
        "event": event,
    }
    payload.update({k: v for k, v in fields.items() if v is not None})
    _logger(logger_name).log(level, json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str))
    return payload
