#!/usr/bin/env python3
"""Focused tests for WearabLLM v3 bench target diagnostics."""

from __future__ import annotations

import unittest

from bench_doctor import analyze_bridge_target, normalize_bridge_base_url


class BridgeTargetTest(unittest.TestCase):
    def test_normalizes_query_endpoint(self) -> None:
        self.assertEqual(
            normalize_bridge_base_url("http://192.168.1.3:8765/v1/query"),
            "http://192.168.1.3:8765",
        )

    def test_matching_ipv4_target(self) -> None:
        result = analyze_bridge_target(
            "http://192.168.1.3:8765/v1/query", ["192.168.1.3"]
        )
        self.assertTrue(result["matches_local"])
        self.assertIsNone(result["suggested_host"])

    def test_stale_ipv4_target_suggests_current_address(self) -> None:
        result = analyze_bridge_target(
            "http://192.0.2.10:8765/v1/query", ["192.0.2.11"]
        )
        self.assertFalse(result["matches_local"])
        self.assertEqual(result["suggested_host"], "192.0.2.11")

    def test_hostname_target_is_unknown(self) -> None:
        result = analyze_bridge_target(
            "http://wearabllm.local:8765/v1/query", ["192.168.1.3"]
        )
        self.assertIsNone(result["matches_local"])

    def test_localhost_is_invalid_for_device_bridge(self) -> None:
        result = analyze_bridge_target(
            "http://localhost:8765/v1/query", ["192.168.1.3"]
        )
        self.assertFalse(result["matches_local"])


if __name__ == "__main__":
    unittest.main()
