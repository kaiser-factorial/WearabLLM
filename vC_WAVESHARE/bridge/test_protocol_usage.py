from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from protocol_usage import ProtocolUsageRecorder, normalize_client_identity, route_family


NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)


class ProtocolUsageRecorderTest(unittest.TestCase):
    def recorder(self) -> ProtocolUsageRecorder:
        return ProtocolUsageRecorder(now=lambda: NOW)

    def test_identity_is_allowlisted_and_release_shaped(self) -> None:
        self.assertEqual(normalize_client_identity(" Android ", "0.1.0"), ("android", "0.1.0"))
        self.assertEqual(normalize_client_identity("waveshare", "1"), ("waveshare", "1"))
        self.assertEqual(
            normalize_client_identity("person@example.com", "private-device-name"),
            ("unknown", "unknown"),
        )
        self.assertEqual(
            normalize_client_identity("android", "private-device-name"),
            ("android", "unknown"),
        )

    def test_local_snapshot_aggregates_only_bounded_dimensions(self) -> None:
        recorder = self.recorder()
        values = {
            "protocol_version": 2,
            "route_family_value": "query_text",
            "method": "POST",
            "status": 200,
            "client_name": "android",
            "client_version": "0.1.0",
        }
        recorder.record(**values)
        recorder.record(**values)
        recorder.record(**{**values, "status": 401})

        snapshot = recorder.snapshot(days=1)

        self.assertEqual(snapshot["backend"], "memory-aggregate")
        self.assertEqual(snapshot["pending_requests"], 3)
        self.assertEqual(sum(row["request_count"] for row in snapshot["rows"]), 3)
        self.assertEqual({row["status_class"] for row in snapshot["rows"]}, {"2xx", "4xx"})
        allowed = {
            "day",
            "protocol_version",
            "route_family",
            "method",
            "status_class",
            "client_name",
            "client_version",
            "request_count",
        }
        self.assertTrue(all(set(row) == allowed for row in snapshot["rows"]))
        serialized = repr(snapshot["rows"]).lower()
        for forbidden in ("device_id", "request_id", "transcript", "query_string"):
            self.assertNotIn(forbidden, serialized)

    def test_route_family_uses_handler_identity_not_raw_path(self) -> None:
        self.assertEqual(route_family("_handle_get_interaction"), "interaction_status")
        self.assertEqual(route_family("/v1/interactions/private-id"), "unknown")

    def test_failed_durable_flush_restores_counts_without_request_failure(self) -> None:
        recorder = self.recorder()
        recorder.supabase_url = "https://example.supabase.co"
        recorder.supabase_service_role_key = "service-role-placeholder"
        recorder._request = Mock(side_effect=OSError("offline"))  # type: ignore[method-assign]
        recorder.record(
            protocol_version=1,
            route_family_value="health",
            method="GET",
            status=200,
            client_name="bench-doctor",
            client_version="0.1.0",
        )

        with self.assertRaises(OSError):
            recorder.flush()

        self.assertEqual(recorder.pending_count(), 1)

    def test_durable_flush_payload_contains_only_aggregate_rows(self) -> None:
        recorder = self.recorder()
        recorder.supabase_url = "https://example.supabase.co"
        recorder.supabase_service_role_key = "service-role-placeholder"
        recorder._request = Mock(return_value=None)  # type: ignore[method-assign]
        recorder.record(
            protocol_version=2,
            route_family_value="conversation",
            method="GET",
            status=200,
            client_name="web-console",
            client_version="0.1.0",
        )

        self.assertEqual(recorder.flush(), 1)

        _, _, payload = recorder._request.call_args.args  # type: ignore[attr-defined]
        self.assertEqual(payload["p_principal_id"], "primary")
        self.assertEqual(len(payload["p_rows"]), 1)
        self.assertEqual(payload["p_rows"][0]["client_name"], "web-console")
        self.assertNotIn("device_id", payload["p_rows"][0])

    def test_shipped_clients_declare_the_non_secret_identity_headers(self) -> None:
        root = Path(__file__).resolve().parents[1]
        expected = {
            root / "app/src/protocol/bridgeClient.ts": "android",
            root / "transcript_viewer/server.py": "web-console",
            root / "scripts/bridge_smoke.sh": "bench-smoke",
            root / "scripts/bench_doctor.py": "bench-doctor",
            root / "scripts/preflight.sh": "preflight",
            root / "firmware/main/main.c": "waveshare",
        }
        for path, client_name in expected.items():
            with self.subTest(path=path):
                source = path.read_text(encoding="utf-8")
                self.assertIn("X-WearabLLM-Client", source)
                self.assertIn("X-WearabLLM-Client-Version", source)
                self.assertIn(client_name, source)


if __name__ == "__main__":
    unittest.main()
