from __future__ import annotations

import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace

import wearabllm_bridge
from http_transport import (
    GET_PATTERN_ROUTES,
    GET_ROUTES,
    POST_PATTERN_ROUTES,
    POST_ROUTES,
    make_handler,
    match_route,
    required_string_field,
    string_field,
)


class RouteMatchingTest(unittest.TestCase):
    def test_exact_routes_report_auth_without_touching_state(self) -> None:
        health = match_route("GET", "/health")
        query = match_route("POST", "/v1/query_text")

        assert health is not None
        assert query is not None
        self.assertEqual(health.endpoint, "_handle_health")
        self.assertFalse(health.auth_required)
        self.assertEqual(query.endpoint, "_handle_text_query")
        self.assertTrue(query.auth_required)

    def test_pattern_routes_return_only_validated_path_arguments(self) -> None:
        action_id = "00000000-0000-0000-0000-000000000000"

        claim = match_route("GET", "/v1/devices/wearabllm-esp32/actions")
        ack = match_route(
            "POST",
            f"/v1/devices/wearabllm-esp32/actions/{action_id}/ack",
        )

        assert claim is not None
        assert ack is not None
        self.assertEqual(claim.path_arguments, ("wearabllm-esp32",))
        self.assertEqual(ack.path_arguments, ("wearabllm-esp32", action_id))
        self.assertIsNone(match_route("POST", "/v1/devices/not valid/actions"))
        self.assertIsNone(match_route("DELETE", "/v1/query_text"))

    def test_v2_routes_share_handlers_but_report_their_protocol_version(self) -> None:
        query = match_route("POST", "/v2/query_text")
        health = match_route("GET", "/v2/health")
        action_id = "00000000-0000-0000-0000-000000000000"
        ack = match_route(
            "POST",
            f"/v2/devices/wearabllm-esp32/actions/{action_id}/ack",
        )

        assert query is not None
        assert health is not None
        assert ack is not None
        self.assertEqual(query.endpoint, "_handle_text_query")
        self.assertEqual(health.endpoint, "_handle_health")
        self.assertEqual(ack.path_arguments, ("wearabllm-esp32", action_id))
        self.assertEqual(query.protocol_version, 2)
        self.assertEqual(health.protocol_version, 2)
        self.assertEqual(ack.protocol_version, 2)
        self.assertEqual(match_route("POST", "/v1/query_text").protocol_version, 1)  # type: ignore[union-attr]

    def test_every_route_targets_a_handler_endpoint(self) -> None:
        handler = make_handler(
            SimpleNamespace(),
        )
        routes = [
            *GET_ROUTES.values(),
            *GET_PATTERN_ROUTES,
            *POST_ROUTES.values(),
            *POST_PATTERN_ROUTES,
        ]

        self.assertTrue(issubclass(handler, BaseHTTPRequestHandler))
        for route in routes:
            with self.subTest(endpoint=route.endpoint):
                self.assertTrue(callable(getattr(handler, route.endpoint, None)))


class TransportBoundaryTest(unittest.TestCase):
    def test_string_fields_reject_non_string_transport_values(self) -> None:
        self.assertEqual(string_field({"transcript": "  hello  "}, "transcript"), "hello")
        self.assertEqual(string_field({"transcript": {"nested": True}}, "transcript"), "")
        with self.assertRaisesRegex(ValueError, "Missing transcript"):
            required_string_field(
                {"transcript": 42},
                "transcript",
                error_message="Missing transcript",
            )

    def test_legacy_make_handler_is_a_narrow_compatibility_adapter(self) -> None:
        handler = wearabllm_bridge.make_handler(SimpleNamespace())

        self.assertTrue(issubclass(handler, BaseHTTPRequestHandler))

    def test_parsing_and_auth_have_one_implementation(self) -> None:
        transport = (Path(__file__).parent / "http_transport.py").read_text(
            encoding="utf-8"
        )
        bridge = (Path(__file__).parent / "wearabllm_bridge.py").read_text(
            encoding="utf-8"
        )

        self.assertEqual(transport.count("json.loads("), 1)
        self.assertEqual(transport.count("hmac.compare_digest("), 1)
        self.assertEqual(transport.count("def _device_id("), 1)
        self.assertNotIn("class Handler(BaseHTTPRequestHandler)", bridge)
        self.assertNotIn("json.loads(self.rfile", bridge)
        self.assertNotIn("state.action_queue", transport)
        self.assertNotIn("state.conversation_store", transport)
        self.assertNotIn("subprocess", transport)
        self.assertIn("authorize_admin_operation", transport)
        self.assertIn("authorize_target_access", transport)


if __name__ == "__main__":
    unittest.main()
