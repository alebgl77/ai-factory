"""Coverage-guided fuzzing of proxy IP normalization and safe log values (MIT)."""

from __future__ import annotations

import ipaddress
import string
import sys

import atheris

with atheris.instrument_imports():
    from app.security import SecurityConfig, _parse_ip, _safe_peer


_ALLOWED = frozenset(string.ascii_letters + string.digits + ".:_-")
_TRUST = SecurityConfig(trusted_networks=(ipaddress.ip_network("192.0.2.0/24"),
                                         ipaddress.ip_network("2001:db8::/32")))


def _expected_ip(value: str | None):
    if not value:
        return None
    candidate = value.strip()
    while candidate and candidate[0] in "[]":
        candidate = candidate[1:]
    while candidate and candidate[-1] in "[]":
        candidate = candidate[:-1]
    try:
        address = ipaddress.IPv4Address(candidate)
    except ipaddress.AddressValueError:
        try:
            address = ipaddress.IPv6Address(candidate)
        except ipaddress.AddressValueError:
            return None
    # Check the mapped prefix numerically, independently of ipv4_mapped.
    if address.version == 6 and int(address) >> 32 == 0xFFFF:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return address


def _check_peer(value: str | None) -> None:
    expected = value if value and 1 <= len(value) <= 64 and all(c in _ALLOWED for c in value) else "unknown"
    assert _safe_peer(value) == expected, "unsafe or truncated peer logging value"
    address = _expected_ip(value)
    assert _parse_ip(value) == address, "IP normalization changed address identity"
    trusted = address is not None and any(address in net for net in _TRUST.trusted_networks)
    assert _TRUST.trusts(value) == trusted, "proxy trust disagrees with normalized IP"


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    text = data.decode("utf-8", "surrogateescape")
    _check_peer(text)
    _check_peer(None)
    v4 = str(ipaddress.IPv4Address(int.from_bytes(data[:4].ljust(4, b"\x00"), "big")))
    v6 = str(ipaddress.IPv6Address(int.from_bytes(data[:16].ljust(16, b"\x00"), "big")))
    for address in (v4, v6, "::ffff:" + v4, "192.0.2.1", "2001:db8::1"):
        _check_peer(address)
        _check_peer(" [" + address + "] ")
    # Exercise log framing, length and non-ASCII boundaries even from short inputs.
    _check_peer(v4 + "\n")
    _check_peer(text[:64])
    _check_peer("a" * 64)
    _check_peer("a" * 65)


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
