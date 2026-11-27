"""Coverage-guided fuzzing of the real LiteLLM spend-metadata boundary (MIT)."""

from __future__ import annotations

import json
import math
import sys

import atheris

with atheris.instrument_imports():
    from app.connectors.base import SourceError
    from app.connectors.litellm import _metadata_object


def _expected(text: str) -> dict | None:
    """Use a separate duplicate-key oracle, including nested and escaped keys."""
    duplicate = False

    def object_pairs(pairs):
        nonlocal duplicate
        seen = set()
        for key, _ in pairs:
            if key in seen:
                duplicate = True
            seen.add(key)
        return dict(pairs)

    if len(text) > 65536:
        return None
    try:
        value = json.loads(text, object_pairs_hook=object_pairs)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) and not duplicate else None


def _equivalent(actual: dict, expected: dict) -> None:
    # Iteration avoids making the oracle more recursion-sensitive than the parser.
    pending = [(actual, expected)]
    while pending:
        left, right = pending.pop()
        assert type(left) is type(right), (type(left), type(right))
        if isinstance(left, dict):
            assert left.keys() == right.keys()
            pending.extend((left[key], right[key]) for key in left)
        elif isinstance(left, list):
            assert len(left) == len(right)
            pending.extend(zip(left, right))
        elif isinstance(left, float) and math.isnan(left):
            assert math.isnan(right)
        else:
            assert left == right


def _check(text: str) -> None:
    expected = _expected(text)
    try:
        actual = _metadata_object(text)
    except SourceError as exc:
        assert expected is None, "valid unique JSON object was rejected"
        assert str(exc) == "litellm: invalid spend metadata"
    else:
        assert expected is not None, "ambiguous, oversized or nonobject metadata was accepted"
        _equivalent(actual, expected)


def _reject(value) -> None:
    try:
        _metadata_object(value)
    except SourceError as exc:
        assert str(exc) == "litellm: invalid spend metadata"
    else:
        raise AssertionError("nonobject metadata was accepted")


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    # surrogateescape preserves malformed UTF-8 as adversarial Python text.
    text = data.decode("utf-8", "surrogateescape")
    _check(text)
    key = json.dumps(text[:64], ensure_ascii=True)
    mode = data[0] % 8 if data else 0
    if mode == 0:
        _check("{" + key + ":1," + key + ":2}")
        _check('{"nested":{' + key + ":1," + key + ":2}}")
    elif mode == 1:
        expected = {text[:64]: [text[64:128], len(data), True, None], "nested": {"value": text[:32]}}
        _equivalent(_metadata_object(json.dumps(expected)), expected)
    elif mode == 2:
        for value in ("null", "false", "[]", json.dumps(text[:64]), str(len(data))):
            _reject(value)
    elif mode == 3:
        # Both sides of the actual character bound, without a large checked-in corpus.
        payload = json.dumps({"value": text[:64]})
        boundary = payload + " " * (65536 - len(payload))
        _check(boundary)
        _reject(boundary + " ")
    elif mode == 4:
        depth = int.from_bytes(data[:2], "little") % 1100
        _check('{"nested":' * depth + "{}" + "}" * depth)
    elif mode == 5:
        for value in (data, len(data), [text[:32]], True):
            _reject(value)
    elif mode == 6:
        value = {"value": text[:64]}
        assert _metadata_object(value) is value
        assert _metadata_object(None) == {}
    else:
        # Escaping changes representation, never the identity of a JSON key.
        _check('{"usecase":1,"\\u0075secase":2}')
        _check('{"nested":{"usecase":1,"\\u0075secase":2}}')


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
