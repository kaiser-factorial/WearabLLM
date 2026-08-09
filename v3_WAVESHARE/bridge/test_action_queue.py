from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from action_queue import JsonActionQueue, SupabaseActionQueue


class JsonActionQueueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "actions.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_create_claim_ack_and_reload_are_durable(self) -> None:
        queue = JsonActionQueue(self.path, lease_seconds=10)
        action, created = queue.create(
            origin_device_id="wearabllm-android",
            target_device_id="wearabllm-esp32",
            transcript="Tell the roomies dinner is ready.",
            command="GS",
            reply="Dinner is ready.",
            idempotency_key="phone-dinner-1",
        )
        self.assertTrue(created)
        self.assertEqual(action["status"], "queued")

        duplicate, created = queue.create(
            origin_device_id="wearabllm-android",
            target_device_id="wearabllm-esp32",
            transcript="Different retry body is ignored.",
            command="RF",
            reply="Ignored.",
            idempotency_key="phone-dinner-1",
        )
        self.assertFalse(created)
        self.assertEqual(duplicate["id"], action["id"])

        claimed = queue.claim_next("wearabllm-esp32")
        assert claimed is not None
        self.assertEqual(claimed["id"], action["id"])
        self.assertEqual(claimed["status"], "dispatched")
        self.assertEqual(claimed["attempts"], 1)

        delivered = queue.acknowledge("wearabllm-esp32", str(action["id"]), "delivered")
        self.assertEqual(delivered["status"], "delivered")
        rendered = queue.acknowledge("wearabllm-esp32", str(action["id"]), "rendered")
        self.assertEqual(rendered["status"], "rendered")
        tts_started = queue.acknowledge("wearabllm-esp32", str(action["id"]), "tts_started")
        self.assertEqual(tts_started["status"], "tts_started")
        played = queue.acknowledge("wearabllm-esp32", str(action["id"]), "played")
        self.assertEqual(played["status"], "played")

        reloaded = JsonActionQueue(self.path)
        persisted = reloaded.get(str(action["id"]))
        assert persisted is not None
        self.assertEqual(persisted["status"], "played")

    def test_only_target_device_can_acknowledge(self) -> None:
        queue = JsonActionQueue(self.path)
        action, _created = queue.create(
            origin_device_id="web-console",
            target_device_id="wearabllm-esp32",
            transcript="Hello",
            command="BS",
            reply="Hello.",
        )
        with self.assertRaises(LookupError):
            queue.acknowledge("wearabllm-other", str(action["id"]), "played")


class SupabaseActionQueueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.queue = SupabaseActionQueue(
            "https://example.supabase.co",
            "service-role-test",
            principal_id="home",
            lease_seconds=30,
        )

    @staticmethod
    def response(payload):
        result = MagicMock()
        result.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
        return result

    @staticmethod
    def row(**overrides):
        record = {
            "id": "11111111-1111-4111-8111-111111111111",
            "principal_id": "home",
            "origin_device_id": "wearabllm-android",
            "target_device_id": "wearabllm-esp32",
            "transcript": "Tell everyone dinner is ready.",
            "command": "GS",
            "reply": "Dinner is ready.",
            "status": "queued",
            "idempotency_key": "phone-dinner-1",
            "delivery_attempts": 0,
            "created_at": "2026-08-09T12:00:00Z",
            "updated_at": "2026-08-09T12:00:00Z",
            "lease_expires_at": None,
            "error": None,
        }
        record.update(overrides)
        return record

    @patch("action_queue.urllib.request.urlopen")
    def test_create_posts_service_role_only_action(self, urlopen):
        urlopen.side_effect = [self.response([]), self.response([self.row()])]
        action, created = self.queue.create(
            origin_device_id="wearabllm-android",
            target_device_id="wearabllm-esp32",
            transcript="Tell everyone dinner is ready.",
            command="GS",
            reply="Dinner is ready.",
            idempotency_key="phone-dinner-1",
        )
        self.assertTrue(created)
        self.assertEqual(action["status"], "queued")
        request = urlopen.call_args_list[1].args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Apikey"), "service-role-test")
        self.assertIn(b'"principal_id": "home"', request.data)

    @patch("action_queue.urllib.request.urlopen")
    def test_claim_uses_atomic_rpc_and_maps_delivery_attempts(self, urlopen):
        urlopen.return_value = self.response([
            self.row(status="dispatched", delivery_attempts=2, lease_expires_at="2026-08-09T12:00:30Z")
        ])
        action = self.queue.claim_next("wearabllm-esp32")
        assert action is not None
        self.assertEqual(action["status"], "dispatched")
        self.assertEqual(action["attempts"], 2)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertIn("/rest/v1/rpc/wearabllm_claim_next_device_action", request.full_url)
        self.assertIn(b'"p_lease_seconds": 30', request.data)

    @patch("action_queue.urllib.request.urlopen")
    def test_acknowledge_marks_played_and_clears_lease(self, urlopen):
        urlopen.side_effect = [
            self.response([self.row(status="dispatched")]),
            self.response([self.row(status="played", played_at="2026-08-09T12:00:10Z")]),
        ]
        action = self.queue.acknowledge(
            "wearabllm-esp32",
            "11111111-1111-4111-8111-111111111111",
            "played",
        )
        self.assertEqual(action["status"], "played")
        request = urlopen.call_args_list[1].args[0]
        self.assertEqual(request.get_method(), "PATCH")
        self.assertIn(b'"status": "played"', request.data)
        self.assertIn(b'"lease_expires_at": null', request.data)


if __name__ == "__main__":
    unittest.main()
