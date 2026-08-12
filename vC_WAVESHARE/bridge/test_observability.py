from __future__ import annotations

import json
import unittest

from observability import (
    REQUEST_ID_RE,
    emit_debug_content,
    emit_event,
    emit_exception,
    new_request_id,
)


class ObservabilityTest(unittest.TestCase):
    def test_request_ids_are_unique_server_generated_hex(self) -> None:
        first = new_request_id()
        second = new_request_id()
        self.assertRegex(first, REQUEST_ID_RE)
        self.assertRegex(second, REQUEST_ID_RE)
        self.assertNotEqual(first, second)

    def test_metadata_event_is_compact_json(self) -> None:
        lines: list[str] = []
        emit_event(
            "http.request_complete",
            sink=lines.append,
            request_id="a" * 32,
            method="POST",
            route="/v1/query_text",
            status=200,
            duration_ms=1.23456,
            request_bytes=42,
            response_bytes=87,
            device_id="web-console",
        )
        payload = json.loads(lines[0])
        self.assertEqual(payload["event"], "http.request_complete")
        self.assertEqual(payload["duration_ms"], 1.235)
        self.assertNotIn(" ", lines[0])

    def test_metadata_path_rejects_content_and_secret_fields(self) -> None:
        for field in ("transcript", "reply", "text", "password", "ssid", "api_key", "authorization"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                emit_event("unsafe.event", sink=lambda _line: None, **{field: "do not log me"})

    def test_metadata_path_rejects_multiline_or_unbounded_values(self) -> None:
        with self.assertRaises(ValueError):
            emit_event("unsafe.event", sink=lambda _line: None, route="/v1/query\nsecret")
        with self.assertRaises(ValueError):
            emit_event("unsafe.event", sink=lambda _line: None, route="x" * 161)

    def test_exception_event_never_includes_exception_message(self) -> None:
        lines: list[str] = []
        secret = "provider-secret-test-marker"
        emit_exception(
            "bridge.provider_failed",
            RuntimeError(secret),
            sink=lines.append,
        )
        payload = json.loads(lines[0])
        self.assertEqual(payload["error_type"], "RuntimeError")
        self.assertNotIn(secret, lines[0])

    def test_content_logging_is_a_separate_explicit_path(self) -> None:
        lines: list[str] = []
        emit_debug_content(
            "debug.query_content",
            transcript="private transcript",
            reply="private reply",
            sink=lines.append,
        )
        payload = json.loads(lines[0])
        self.assertEqual(payload["privacy"], "content")
        self.assertEqual(payload["transcript"], "private transcript")
        self.assertEqual(payload["reply"], "private reply")


if __name__ == "__main__":
    unittest.main()
