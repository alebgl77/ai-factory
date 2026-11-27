"""Regression and mutation checks proving the fuzz assertions detect real violations (MIT)."""

import ipaddress
import json
import unittest
from unittest.mock import patch

import fuzz_metadata
import fuzz_peers


class HarnessTests(unittest.TestCase):
    def test_metadata_edges(self):
        for value in ('{}', '{"nested":{"usecase":1,"usecase":2}}', '{"a":1,"\\u0061":2}',
                      '[]', '{"text":"\\ud800"}', '{"n":NaN}', '{"text":"é"}', '\udcff'):
            fuzz_metadata._check(value)
        for mode in range(8):
            fuzz_metadata.TestOneInput(bytes([mode, 255, 254, 128]))

    def test_metadata_recursion_boundary(self):
        for depth in (0, 1, 100, 900, 990, 1000, 1100):
            fuzz_metadata._check('{"nested":' * depth + "{}" + "}" * depth)

    def test_peer_edges(self):
        for value in (None, "", "192.0.2.1\n", "::ffff:192.0.2.1", " [2001:db8::1] ",
                      "fe80::1%eth0", "127.000.0.1", "peer\r\nforged", "\udcff", "a" * 64, "a" * 65):
            fuzz_peers._check_peer(value)

    def test_duplicate_acceptance_mutation_is_detected(self):
        with patch.object(fuzz_metadata, "_metadata_object", json.loads):
            with self.assertRaisesRegex(AssertionError, "ambiguous"):
                fuzz_metadata._check('{"usecase":"one","usecase":"two"}')

    def test_log_allowlist_mutation_is_detected(self):
        with patch.object(fuzz_peers, "_safe_peer", lambda value: value or "unknown"):
            with self.assertRaisesRegex(AssertionError, "unsafe"):
                fuzz_peers._check_peer("192.0.2.1\n")

    def test_mapped_ip_mutation_is_detected(self):
        with patch.object(fuzz_peers, "_parse_ip", lambda value: ipaddress.IPv6Address(value)):
            with self.assertRaisesRegex(AssertionError, "normalization"):
                fuzz_peers._check_peer("::ffff:192.0.2.1")


if __name__ == "__main__":
    unittest.main()
