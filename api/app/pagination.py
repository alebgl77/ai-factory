"""Signed continuations bound to a verified request scope and query."""

from __future__ import annotations

import base64
import binascii
import hmac
import json
from typing import Annotated, Any

from fastapi import Query


Cursor = Annotated[str, Query(max_length=256, pattern=r"^[A-Za-z0-9_-]+\.[0-9a-f]{64}$")]


def encode_cursor(key: bytes, scope: list[Any], position: list[Any]) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(position, separators=(",", ":")).encode()).decode().rstrip("=")
    return payload + "." + _signature(key, scope, payload)


def decode_cursor(key: bytes, scope: list[Any], cursor: str) -> list[Any] | None:
    payload, separator, signature = cursor.partition(".")
    if not separator or not hmac.compare_digest(signature, _signature(key, scope, payload)):
        return None
    try:
        position = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (binascii.Error, ValueError):
        return None
    return position if isinstance(position, list) else None


def _signature(key: bytes, scope: list[Any], payload: str) -> str:
    context = json.dumps(scope, separators=(",", ":")).encode()
    return hmac.digest(key, b"factory-page-v1\0" + context + b"\0" + payload.encode(), "sha256").hex()
