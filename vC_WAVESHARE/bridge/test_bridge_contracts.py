from __future__ import annotations

import json
import tempfile
import threading
import unittest
from argparse import Namespace
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from observability import REQUEST_ID_HEADER, REQUEST_ID_RE
from wearabllm_bridge import BridgeState, make_handler, make_silence_wav


FIXTURES = json.loads(
    (Path(__file__).parent / "contract_fixtures" / "v1" / "golden_shapes.json").read_text(
        encoding="utf-8"
    )
)


class BridgeContractTest(unittest.TestCase):
    """Golden HTTP contracts captured before the bridge is decomposed."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.events: list[str] = []
        self.state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                stt_model="gpt-4o-transcribe",
                llm_model="gpt-5.4-mini",
                tts_model="gpt-4o-mini-tts",
                tts_voice="alloy",
                tts_instructions="Contract-test speech instructions.",
                history_turns=20,
                max_output_tokens=512,
                session_idle_seconds=3600,
                conversation_backend="local",
                conversation_file=str(root / "conversations.json"),
                durable_memory=False,
                memory_backend="local",
                memory_file=str(root / "memory.json"),
                memory_retrieval_limit=3,
                web_search=False,
                max_tool_rounds=8,
                device_id="wearabllm-unknown",
                device_token="contract-token",
                action_backend="local",
                action_queue_file=str(root / "actions.json"),
                action_lease_seconds=45,
                agent_config_file=str(root / "agent_config.json"),
                save_wav_dir="",
                allow_device_config=False,
                debug_content_logs=False,
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                max_audio_bytes=64 * 1024,
            )
        )
        self.state.openai_client = SimpleNamespace(
            models=SimpleNamespace(
                list=lambda: SimpleNamespace(
                    data=[
                        SimpleNamespace(id="gpt-5.4-mini"),
                        SimpleNamespace(id="gpt-4o-mini-tts"),
                    ]
                )
            )
        )
        handler = make_handler(self.state, event_sink=self.events.append)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp_dir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        authorized: bool = True,
        device_id: str = "web-console",
    ) -> tuple[int, dict[str, str], bytes]:
        request_headers = dict(headers or {})
        if authorized:
            request_headers.setdefault("X-WearabLLM-Device-Token", "contract-token")
        if device_id:
            request_headers.setdefault("X-WearabLLM-Device-Id", device_id)
        connection = HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        response_body = response.read()
        response_headers = {name: value for name, value in response.getheaders()}
        status = response.status
        connection.close()
        request_id = response_headers.get(REQUEST_ID_HEADER, "")
        self.assertRegex(request_id, REQUEST_ID_RE)
        matching_events = [
            json.loads(line)
            for line in self.events
            if json.loads(line).get("event") == "http.request_complete"
            and json.loads(line).get("request_id") == request_id
        ]
        self.assertEqual(len(matching_events), 1)
        self.assertEqual(matching_events[0]["status"], status)
        return status, response_headers, response_body

    def request_json(self, *args: object, **kwargs: object) -> tuple[int, dict[str, str], dict[str, object]]:
        status, headers, body = self.request(*args, **kwargs)  # type: ignore[arg-type]
        return status, headers, json.loads(body.decode("utf-8"))

    def assert_fixture(
        self,
        name: str,
        status: int,
        headers: dict[str, str],
        body: bytes | dict[str, object],
    ) -> None:
        fixture = FIXTURES[name]
        if "status" in fixture:
            self.assertEqual(status, fixture["status"])
        self.assertEqual(headers.get("Content-Type"), fixture["content_type"])
        if "body_prefix" in fixture:
            assert isinstance(body, bytes)
            self.assertEqual(body[:4].decode("ascii"), fixture["body_prefix"])
            return
        assert isinstance(body, dict)
        self.assertEqual(sorted(body), fixture["top_level_keys"])
        for key, expected in fixture.get("required_values", {}).items():
            self.assertEqual(body[key], expected)
        for key, expected_keys in fixture.get("nested_keys", {}).items():
            nested = body[key]
            self.assertIsInstance(nested, dict)
            self.assertEqual(sorted(nested), expected_keys)

    def test_health_query_and_tts_golden_contracts(self) -> None:
        status, headers, payload = self.request_json("GET", "/health", authorized=False)
        self.assert_fixture("health.success", status, headers, payload)

        private_text = "phase-zero-private-transcript"
        status, headers, payload = self.request_json(
            "POST",
            "/v1/query_text",
            body=json.dumps({"transcript": private_text}).encode(),
            headers={"Content-Type": "application/json"},
        )
        self.assert_fixture("query.success", status, headers, payload)
        self.assertNotIn(private_text, "\n".join(self.events))

        wav = make_silence_wav(100)
        status, headers, payload = self.request_json(
            "POST",
            "/v1/query",
            body=wav,
            headers={"Content-Type": "audio/wav"},
            device_id="wearabllm-esp32",
        )
        self.assert_fixture("query.success", status, headers, payload)

        status, headers, body = self.request(
            "POST",
            "/v1/tts",
            body=b'{"text":"speak this privately"}',
            headers={"Content-Type": "application/json"},
        )
        self.assert_fixture("tts.success", status, headers, body)
        self.assertNotIn("speak this privately", "\n".join(self.events))

    def test_query_and_tts_malformed_contracts(self) -> None:
        cases = [
            ("/v1/query_text", b"{"),
            ("/v1/query_text", b"[]"),
            ("/v1/tts", b"{"),
            ("/v1/tts", b"[]"),
        ]
        for path, body in cases:
            with self.subTest(path=path, body=body):
                status, headers, payload = self.request_json(
                    "POST", path, body=body, headers={"Content-Type": "application/json"}
                )
                self.assertEqual(status, 400)
                self.assert_fixture("error.legacy", status, headers, payload)

        status, headers, payload = self.request_json(
            "POST",
            "/v1/query",
            body=b"x" * (self.state.args.max_audio_bytes + 1),
            headers={"Content-Type": "audio/wav"},
        )
        self.assertEqual(status, 413)
        self.assert_fixture("error.legacy", status, headers, payload)

        status, headers, payload = self.request_json(
            "POST",
            "/v1/interactions",
            body=b'{"transcript":"missing target"}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assert_fixture("error.legacy", status, headers, payload)

    def test_interaction_list_get_claim_and_ack_contracts(self) -> None:
        status, headers, created = self.request_json(
            "POST",
            "/v1/interactions",
            body=json.dumps(
                {
                    "transcript": "A private delivery",
                    "origin_device_id": "web-console",
                    "target_device_id": "wearabllm-esp32",
                    "idempotency_key": "phase-zero-contract",
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        self.assert_fixture("interaction.success", status, headers, created)
        action = created["action"]
        assert isinstance(action, dict)
        action_id = str(action["id"])

        status, _, listed = self.request_json("GET", "/v1/interactions?limit=10")
        self.assertEqual(status, 200)
        self.assertEqual(sorted(listed), ["actions", "ok"])

        status, _, fetched = self.request_json("GET", f"/v1/interactions/{action_id}")
        self.assertEqual(status, 200)
        self.assertEqual(sorted(fetched), ["action", "ok"])

        status, _, mismatch = self.request_json(
            "GET",
            "/v1/devices/wearabllm-esp32/actions",
            device_id="wearabllm-android",
        )
        self.assertEqual(status, 403)
        self.assertEqual(sorted(mismatch), ["error"])

        status, _, claimed = self.request_json(
            "GET",
            "/v1/devices/wearabllm-esp32/actions",
            device_id="wearabllm-esp32",
        )
        self.assertEqual(status, 200)
        self.assertEqual(claimed["action"]["status"], "dispatched")  # type: ignore[index]

        status, _, denied = self.request_json(
            "POST",
            f"/v1/devices/wearabllm-esp32/actions/{action_id}/ack",
            body=b'{"status":"played"}',
            headers={"Content-Type": "application/json"},
            device_id="wearabllm-android",
        )
        self.assertEqual(status, 403)
        self.assertEqual(sorted(denied), ["error"])

        status, _, acknowledged = self.request_json(
            "POST",
            f"/v1/devices/wearabllm-esp32/actions/{action_id}/ack",
            body=b'{"status":"played"}',
            headers={"Content-Type": "application/json"},
            device_id="wearabllm-esp32",
        )
        self.assertEqual(status, 200)
        self.assertEqual(acknowledged["action"]["status"], "played")  # type: ignore[index]
        audit_events = [json.loads(line) for line in self.events if '"event":"audit.privileged_operation"' in line]
        self.assertTrue(any(item.get("operation") == "action_acknowledge" for item in audit_events))

        status, _, invalid = self.request_json("GET", "/v1/interactions?limit=nope")
        self.assertEqual(status, 400)
        self.assertEqual(sorted(invalid), ["error"])

        status, _, missing = self.request_json(
            "GET", "/v1/interactions/00000000-0000-0000-0000-000000000000"
        )
        self.assertEqual(status, 404)
        self.assertEqual(sorted(missing), ["error"])
        self.assertNotIn("contract-token", "\n".join(self.events))

        status, _, missing_ack = self.request_json(
            "POST",
            "/v1/devices/wearabllm-esp32/actions/00000000-0000-0000-0000-000000000000/ack",
            body=b'{"status":"played"}',
            headers={"Content-Type": "application/json"},
            device_id="wearabllm-esp32",
        )
        self.assertEqual(status, 404)
        self.assertEqual(sorted(missing_ack), ["error"])

    def test_conversation_session_routes_contracts(self) -> None:
        status, _, reset = self.request_json("POST", "/v1/session/reset")
        self.assertEqual(status, 200)
        self.assertEqual(
            sorted(reset),
            ["active_session_id", "ended_session_id", "history_messages", "ok", "saved_turns", "session"],
        )
        session_id = str(reset["active_session_id"])

        status, _, renamed = self.request_json(
            "POST",
            f"/v1/conversation/sessions/{session_id}/rename",
            body=b'{"title":"Contract baseline"}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(sorted(renamed), ["ok", "session"])

        status, headers, conversation = self.request_json("GET", "/v1/conversation")
        self.assert_fixture("conversation.success", status, headers, conversation)

        status, _, invalid_limit = self.request_json("GET", "/v1/conversation?limit=nope")
        self.assertEqual(status, 400)
        self.assertEqual(sorted(invalid_limit), ["error"])

        status, _, sessions = self.request_json("GET", "/v1/conversation/sessions")
        self.assertEqual(status, 200)
        self.assertEqual(sorted(sessions), ["active_session_id", "ok", "sessions"])

        status, _, archived = self.request_json(
            "POST", f"/v1/conversation/sessions/{session_id}/archive"
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            sorted(archived),
            ["active_session_id", "already_archived", "archived_session_id", "archived_turns", "ok", "session"],
        )

        status, _, missing = self.request_json(
            "POST", "/v1/conversation/sessions/00000000-0000-0000-0000-000000000000/archive"
        )
        self.assertEqual(status, 404)
        self.assertEqual(sorted(missing), ["error"])

        status, _, devices = self.request_json("GET", "/v1/devices")
        self.assertEqual(status, 200)
        self.assertEqual(sorted(devices), ["devices", "ok"])

        status, _, invalid_rename = self.request_json(
            "POST",
            f"/v1/conversation/sessions/{session_id}/rename",
            body=b'{"title":""}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(sorted(invalid_rename), ["error"])

        original_reset = self.state.start_new_conversation
        reset_secret = "reset-backend-secret"
        self.state.start_new_conversation = Mock(side_effect=RuntimeError(reset_secret))
        status, _, failed_reset = self.request_json("POST", "/v1/session/reset")
        self.assertEqual(status, 500)
        self.assertEqual(failed_reset, {"ok": False, "error": "Conversation reset failed"})
        self.assertNotIn(reset_secret, "\n".join(self.events))
        self.state.start_new_conversation = original_reset

    def test_sensor_routes_contracts(self) -> None:
        manifest_body = json.dumps(
            {
                "version": 1,
                "firmware_version": "phase-zero",
                "sensors": [
                    {"id": "temperature", "quantity": "temperature", "label": "Board temp", "unit": "C"}
                ],
            }
        ).encode()
        status, headers, manifest = self.request_json(
            "POST",
            "/v1/devices/wearabllm-esp32/sensor-manifest",
            body=manifest_body,
            headers={"Content-Type": "application/json"},
            device_id="wearabllm-esp32",
        )
        self.assert_fixture("sensor_manifest.success", status, headers, manifest)

        status, _, catalog = self.request_json(
            "GET", "/v1/sensors?device_id=wearabllm-esp32"
        )
        self.assertEqual(status, 200)
        self.assertEqual(sorted(catalog), ["devices", "ok"])
        self.assertEqual(len(catalog["devices"]), 1)  # type: ignore[arg-type]

        status, _, denied = self.request_json(
            "POST",
            "/v1/devices/wearabllm-esp32/sensor-manifest",
            body=manifest_body,
            headers={"Content-Type": "application/json"},
            device_id="wearabllm-android",
        )
        self.assertEqual(status, 403)
        self.assertEqual(sorted(denied), ["error"])

        status, _, invalid_filter = self.request_json(
            "GET", "/v1/sensors?device_id=not%20valid"
        )
        self.assertEqual(status, 400)
        self.assertEqual(sorted(invalid_filter), ["error"])

    def test_admin_and_device_config_contracts_are_redacted(self) -> None:
        status, headers, config = self.request_json("GET", "/v1/admin/config")
        self.assert_fixture("admin_config.success", status, headers, config)

        private_instructions = "Private delivery instructions for contract testing."
        status, headers, updated = self.request_json(
            "POST",
            "/v1/admin/config",
            body=json.dumps({"tts_instructions": private_instructions}).encode(),
            headers={"Content-Type": "application/json"},
        )
        self.assert_fixture("admin_config.success", status, headers, updated)
        self.assertNotIn(private_instructions, "\n".join(self.events))

        status, _, invalid = self.request_json(
            "POST",
            "/v1/admin/config",
            body=b"{}",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(sorted(invalid), ["error"])

        status, _, catalog = self.request_json("GET", "/v1/admin/catalog")
        self.assertEqual(status, 200)
        self.assertEqual(sorted(catalog), ["catalog", "ok"])

        configured_client = self.state.openai_client
        self.state.openai_client = None
        status, _, failed_catalog = self.request_json("GET", "/v1/admin/catalog")
        self.assertEqual(status, 502)
        self.assertEqual(failed_catalog, {"error": "OpenAI catalog request failed"})
        self.state.openai_client = configured_client

        api_secret = "test-api-value-never-log"
        self.state.replace_openai_api_key = Mock(
            return_value={"ok": True, "key_storage": "macos-keychain", "catalog": {}}
        )
        status, _, api_result = self.request_json(
            "POST",
            "/v1/admin/api-key",
            body=json.dumps({"api_key": api_secret}).encode(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(sorted(api_result), ["catalog", "key_storage", "ok"])
        self.assertNotIn(api_secret, "\n".join(self.events))

        self.state.replace_openai_api_key = Mock(side_effect=ValueError("API key is required"))
        status, _, invalid_key = self.request_json(
            "POST",
            "/v1/admin/api-key",
            body=b'{"api_key":""}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(sorted(invalid_key), ["error"])

        provider_secret = "provider-error-secret"
        self.state.replace_openai_api_key = Mock(side_effect=RuntimeError(provider_secret))
        status, _, failed_key = self.request_json(
            "POST",
            "/v1/admin/api-key",
            body=b'{"api_key":"test-value"}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 502)
        self.assertEqual(failed_key, {"error": "OpenAI API key update failed"})
        self.assertNotIn(provider_secret, "\n".join(self.events))

        wifi_name = "private-network-name"
        wifi_password = "private-network-password"
        status, headers, disabled = self.request_json(
            "POST",
            "/v1/device_wifi",
            body=json.dumps({"ssid": wifi_name, "password": wifi_password}).encode(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 403)
        self.assert_fixture("device_config_error.legacy", status, headers, disabled)
        self.assertNotIn(wifi_name, "\n".join(self.events))
        self.assertNotIn(wifi_password, "\n".join(self.events))

        status, _, invalid_wifi = self.request_json(
            "POST",
            "/v1/device_wifi",
            body=b"[]",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(sorted(invalid_wifi), ["error"])

        preview_ssid = "preview-private-network"
        preview_password = "preview-private-password"
        status, _, preview = self.request_json(
            "POST",
            "/v1/device_wifi",
            body=json.dumps(
                {
                    "ssid": preview_ssid,
                    "password": preview_password,
                    "preview": True,
                    "display_enabled": True,
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(preview["preview"])
        self.assertTrue(preview["ssid_set"])
        self.assertTrue(preview["password_set"])
        self.assertNotIn(preview_ssid, json.dumps(preview))
        self.assertNotIn(preview_password, json.dumps(preview))
        self.assertNotIn(preview_ssid, "\n".join(self.events))
        self.assertNotIn(preview_password, "\n".join(self.events))

        self.state.configure_device_wifi_request = Mock(
            return_value={
                "ok": True,
                "ssid": wifi_name,
                "bssid": None,
                "password_set": True,
                "message": "Updated ignored firmware/sdkconfig.",
            }
        )
        status, _, configured = self.request_json(
            "POST",
            "/v1/device_wifi",
            body=json.dumps({"ssid": wifi_name, "password": wifi_password}).encode(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(configured["ok"])
        self.assertNotIn(wifi_name, "\n".join(self.events))
        self.assertNotIn(wifi_password, "\n".join(self.events))

    def test_auth_heartbeat_options_and_unknown_contracts(self) -> None:
        status, headers, unauthorized = self.request_json(
            "GET", "/v1/admin/config", authorized=False
        )
        self.assertEqual(status, 401)
        self.assert_fixture("error.legacy", status, headers, unauthorized)

        status, _, heartbeat = self.request_json("POST", "/v1/heartbeat")
        self.assertEqual(status, 200)
        self.assertEqual(heartbeat, {"ok": True, "device_id": "web-console"})

        status, _, invalid_device = self.request_json(
            "POST", "/v1/heartbeat", device_id="not valid"
        )
        self.assertEqual(status, 400)
        self.assertEqual(sorted(invalid_device), ["error"])

        status, headers, body = self.request("OPTIONS", "/v1/query_text", authorized=False)
        self.assertEqual(status, 204)
        self.assertEqual(body, b"")
        self.assertEqual(headers.get("Access-Control-Expose-Headers"), REQUEST_ID_HEADER)

        status, _, missing = self.request_json("POST", "/v1/not-found")
        self.assertEqual(status, 404)
        self.assertEqual(sorted(missing), ["error"])


if __name__ == "__main__":
    unittest.main()
