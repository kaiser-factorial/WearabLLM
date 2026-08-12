from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from action_queue import JsonActionQueue
from sphere_tools import (
    PendingMemoryConfirmationStore,
    SphereToolExecutor,
    forced_memory_mutation_tool_for_turn,
    function_tools,
    memory_confirmation_decision_for_turn,
    sensitive_memory_candidate_for_turn,
    source_read_requested_for_turn,
    web_search_requested_for_turn,
)


class SphereToolExecutorTest(unittest.TestCase):
    def test_source_continuations_route_to_source_read(self) -> None:
        self.assertTrue(source_read_requested_for_turn("yes please, starting line 1201"))
        self.assertTrue(source_read_requested_for_turn("vC_WAVESHARE/bridge/wearabllm_bridge.py"))
        self.assertFalse(source_read_requested_for_turn("What should we build next?"))

    def test_web_routing_does_not_treat_current_profile_data_as_a_search_request(self) -> None:
        self.assertFalse(web_search_requested_for_turn("My current address is 123 Main Street."))
        self.assertTrue(web_search_requested_for_turn("What's the latest OpenAI model today?"))
        self.assertTrue(web_search_requested_for_turn("What is the weather in Oakland?"))

    def test_negative_confirmation_wins_over_save_it_words(self) -> None:
        self.assertFalse(memory_confirmation_decision_for_turn("No, do not save it."))
        self.assertTrue(memory_confirmation_decision_for_turn("Yes, save it."))

    def test_personal_contact_claim_requires_staging_before_confirmation(self) -> None:
        self.assertTrue(sensitive_memory_candidate_for_turn("My home address is 123 Main Street."))
        self.assertTrue(sensitive_memory_candidate_for_turn("My phone is 415-555-1212."))
        self.assertFalse(sensitive_memory_candidate_for_turn("The cafe is at 123 Main Street."))

    def test_explicit_mutation_with_id_selects_auditable_tool(self) -> None:
        memory_id = "6536beea-014a-48a8-b8af-43b0e8dc3cf2"
        self.assertEqual(
            forced_memory_mutation_tool_for_turn(f"Forget the memory with ID {memory_id}."),
            "memory_forget",
        )
        self.assertEqual(
            forced_memory_mutation_tool_for_turn("Forget the memory about frosted quartz."),
            "memory_search",
        )
        self.assertEqual(
            forced_memory_mutation_tool_for_turn("Remember that I prefer jasmine tea."),
            "memory_remember",
        )

    def test_status_tool_is_strict_read_only_and_cross_body(self) -> None:
        tool = next(item for item in function_tools() if item["name"] == "sphere_status")
        self.assertTrue(tool["strict"])
        self.assertEqual(
            tool["parameters"]["required"],
            ["target_device_ids", "include_recent_actions"],
        )
        self.assertFalse(tool["parameters"]["additionalProperties"])

        status_provider = Mock(return_value={"observed_at": "2026-08-11T12:00:00Z", "bodies": []})
        queue = Mock()
        executor = SphereToolExecutor(
            memory_store=None,
            action_queue=queue,
            status_provider=status_provider,
            origin_device_id="wearabllm-android",
            user_transcript="What is Sphere's current state?",
        )
        result = executor.execute(
            "sphere_status",
            {
                "target_device_ids": ["wearabllm-esp32", "web-console"],
                "include_recent_actions": True,
            },
            call_id="call-status",
        )

        self.assertTrue(result["ok"])
        status_provider.assert_called_once_with(
            ["wearabllm-esp32", "web-console"],
            include_recent_actions=True,
        )
        queue.create.assert_not_called()

    def test_status_tool_rejects_unknown_or_path_like_targets(self) -> None:
        status_provider = Mock()
        executor = SphereToolExecutor(
            memory_store=None,
            action_queue=Mock(),
            status_provider=status_provider,
            origin_device_id="web-console",
            user_transcript="Check Sphere status.",
        )
        for target in ("wearabllm-esp32/../../", "attacker-body"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                executor.execute(
                    "sphere_status",
                    {"target_device_ids": [target], "include_recent_actions": False},
                    call_id="call-status-invalid",
                )
        status_provider.assert_not_called()

    @staticmethod
    def sensor_provider(device_id: str = ""):
        manifest = {
            "device_id": "ducati-temp-sensor",
            "firmware_version": "6.4",
            "sensors": [
                {"id": "ambient_temperature", "quantity": "temperature", "label": "Ambient temperature", "unit": "Cel"}
            ],
        }
        return [manifest] if not device_id or device_id == manifest["device_id"] else []

    def test_sensor_tools_are_strict_and_bounded(self) -> None:
        tools = {item["name"]: item for item in function_tools()}
        self.assertEqual(tools["sensor_read"]["parameters"]["properties"]["wait_seconds"]["maximum"], 20)
        self.assertEqual(tools["sensor_loop"]["parameters"]["properties"]["count"]["maximum"], 10)
        self.assertEqual(tools["sensor_loop"]["parameters"]["properties"]["interval_seconds"]["minimum"], 30)
        self.assertTrue(tools["loop_cancel"]["strict"])

    def test_sensor_loop_creates_due_time_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = JsonActionQueue(Path(tmpdir) / "actions.json")
            executor = SphereToolExecutor(
                memory_store=None,
                action_queue=queue,
                sensor_provider=self.sensor_provider,
                origin_device_id="web-console",
                user_transcript="Take five temperature readings every minute.",
            )
            result = executor.execute(
                "sensor_loop",
                {"device_id": "ducati-temp-sensor", "sensor_ids": ["ambient_temperature"], "count": 5, "interval_seconds": 60},
                call_id="call-temperature-loop",
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["count"], 5)
            self.assertEqual(len(result["actions"]), 5)
            actions = queue.list(target_device_id="ducati-temp-sensor")
            self.assertEqual(len(actions), 5)
            self.assertEqual({item["payload"]["schedule_id"] for item in actions}, {result["schedule_id"]})

    def test_sensor_read_never_invents_a_pending_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = JsonActionQueue(Path(tmpdir) / "actions.json")
            executor = SphereToolExecutor(
                memory_store=None,
                action_queue=queue,
                sensor_provider=self.sensor_provider,
                origin_device_id="web-console",
                user_transcript="Take the temperature now.",
            )
            result = executor.execute(
                "sensor_read",
                {"device_id": "ducati-temp-sensor", "sensor_ids": ["ambient_temperature"], "wait_seconds": 0},
                call_id="call-temperature-now",
            )
            self.assertTrue(result["pending"])
            self.assertIsNone(result["result"])

    def test_sensor_read_returns_only_device_acknowledged_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = JsonActionQueue(Path(tmpdir) / "actions.json")
            executor = SphereToolExecutor(
                memory_store=None,
                action_queue=queue,
                sensor_provider=self.sensor_provider,
                origin_device_id="web-console",
                user_transcript="Measure the temperature now.",
            )

            def device() -> None:
                deadline = time.monotonic() + 1.0
                action = None
                while not action and time.monotonic() < deadline:
                    action = queue.claim_next("ducati-temp-sensor")
                    if not action:
                        time.sleep(0.01)
                assert action is not None
                queue.acknowledge(
                    "ducati-temp-sensor",
                    str(action["id"]),
                    "completed",
                    result={"sequence": 12, "uptime_ms": 9000, "readings": [{"sensor_id": "ambient_temperature", "value": 21.75, "unit": "Cel"}]},
                )

            worker = threading.Thread(target=device)
            worker.start()
            result = executor.execute(
                "sensor_read",
                {"device_id": "ducati-temp-sensor", "sensor_ids": ["ambient_temperature"], "wait_seconds": 2},
                call_id="call-temperature-confirmed",
            )
            worker.join(timeout=1)
            self.assertFalse(result["pending"])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["result"]["readings"][0]["value"], 21.75)

    def test_safe_durable_memory_can_be_saved_without_magic_words(self) -> None:
        memory = Mock()
        memory.remember.return_value = ({"id": "memory-1"}, True)
        executor = SphereToolExecutor(
            memory_store=memory,
            action_queue=Mock(),
            origin_device_id="wearabllm-android",
            user_transcript="I like green tea.",
        )
        result = executor.execute(
            "memory_remember",
            {
                "subject": "principal",
                "kind": "preference",
                "content": "The user likes green tea.",
                "tags": ["tea"],
                "importance": 3,
                "expires_at": "",
            },
            call_id="call-memory",
        )

        self.assertTrue(result["created"])
        memory.remember.assert_called_once()

    def test_credentials_are_never_staged_or_saved(self) -> None:
        fake_api_key = "sk-proj-" + ("a" * 32)
        memory = Mock()
        pending = PendingMemoryConfirmationStore()
        audits: list[dict[str, object]] = []

        def audit(operation: str, outcome: str, **fields: object) -> None:
            audits.append({"operation": operation, "outcome": outcome, **fields})

        executor = SphereToolExecutor(
            memory_store=memory,
            action_queue=Mock(),
            pending_memory_confirmations=pending,
            origin_device_id="web-console",
            user_transcript=f"My API key is {fake_api_key}.",
            audit_sink=audit,
        )

        with self.assertRaisesRegex(PermissionError, "credentials"):
            executor.execute(
                "memory_remember",
                {
                    "subject": "principal",
                    "kind": "fact",
                    "content": f"The API key is {fake_api_key}.",
                    "tags": [],
                    "importance": 5,
                    "expires_at": "",
                },
                call_id="call-secret",
            )

        memory.remember.assert_not_called()
        self.assertFalse(pending.has_pending())
        self.assertNotIn(fake_api_key, repr(audits))
        self.assertEqual(audits[0]["operation"], "memory_mutation")
        self.assertEqual(audits[0]["outcome"], "rejected")

    def test_precise_address_requires_bound_yes_confirmation(self) -> None:
        memory = Mock()
        memory.remember.return_value = ({"id": "memory-address"}, True)
        pending = PendingMemoryConfirmationStore()
        first = SphereToolExecutor(
            memory_store=memory,
            action_queue=Mock(),
            pending_memory_confirmations=pending,
            origin_device_id="web-console",
            user_transcript="I live at 123 Main Street.",
        )

        staged = first.execute(
            "memory_remember",
            {
                "subject": "principal",
                "kind": "fact",
                "content": "The user lives at 123 Main Street.",
                "tags": ["address"],
                "importance": 4,
                "expires_at": "",
            },
            call_id="call-address",
        )

        self.assertTrue(staged["confirmation_required"])
        self.assertEqual(staged["sensitive_categories"], ["precise_address"])
        memory.remember.assert_not_called()

        confirm = SphereToolExecutor(
            memory_store=memory,
            action_queue=Mock(),
            pending_memory_confirmations=pending,
            origin_device_id="wearabllm-android",
            user_transcript="Yes, save it.",
        )
        saved = confirm.execute("memory_confirm", {"save": True}, call_id="call-confirm")

        self.assertTrue(saved["created"])
        self.assertFalse(pending.has_pending())
        self.assertEqual(memory.remember.call_args.kwargs["source_device_id"], "web-console")

    def test_no_confirmation_discards_pending_memory(self) -> None:
        memory = Mock()
        pending = PendingMemoryConfirmationStore()
        pending.stage(
            {
                "subject": "principal",
                "kind": "fact",
                "content": "The user lives at 123 Main Street.",
                "tags": ["address"],
                "importance": 4,
                "expires_at": "",
            },
            source_device_id="web-console",
            sensitive_categories=["precise_address"],
        )
        executor = SphereToolExecutor(
            memory_store=memory,
            action_queue=Mock(),
            pending_memory_confirmations=pending,
            origin_device_id="web-console",
            user_transcript="No, don't save that.",
        )

        result = executor.execute("memory_confirm", {"save": False}, call_id="call-no")

        self.assertFalse(result["saved"])
        self.assertFalse(pending.has_pending())
        memory.remember.assert_not_called()

    def test_negative_confirmation_cannot_be_executed_as_save_true(self) -> None:
        pending = PendingMemoryConfirmationStore()
        pending.stage(
            {
                "subject": "principal",
                "kind": "fact",
                "content": "The user lives at 123 Main Street.",
                "tags": [],
                "importance": 4,
                "expires_at": "",
            },
            source_device_id="web-console",
            sensitive_categories=["precise_address"],
        )
        executor = SphereToolExecutor(
            memory_store=Mock(),
            action_queue=Mock(),
            pending_memory_confirmations=pending,
            origin_device_id="web-console",
            user_transcript="No, do not save it.",
        )

        with self.assertRaises(PermissionError):
            executor.execute("memory_confirm", {"save": True}, call_id="call-wrong-decision")
        self.assertTrue(pending.has_pending())

    def test_model_memory_search_is_principal_wide(self) -> None:
        tool = next(item for item in function_tools() if item["name"] == "memory_search")
        self.assertEqual(tool["parameters"]["required"], ["query", "limit"])
        self.assertNotIn("subject", tool["parameters"]["properties"])
        self.assertNotIn("kinds", tool["parameters"]["properties"])

        memory = Mock()
        memory.search.return_value = [{"id": "memory-1"}]
        executor = SphereToolExecutor(
            memory_store=memory,
            action_queue=Mock(),
            origin_device_id="web-console",
            user_transcript="Did you save that?",
        )
        result = executor.execute(
            "memory_search",
            {"query": "Corina's preferred tone and career goal", "limit": 5},
            call_id="call-search",
        )

        self.assertEqual(len(result["memories"]), 1)
        memory.search.assert_called_once_with(
            "Corina's preferred tone and career goal",
            subject="",
            kinds=[],
            limit=5,
        )

    def test_source_tools_are_read_only_and_bounded(self) -> None:
        names = {tool["name"] for tool in function_tools()}
        self.assertIn("source_list", names)
        self.assertIn("source_read", names)
        source = Mock()
        source.list.return_value = [{"path": "vC_WAVESHARE/bridge", "type": "directory"}]
        source.read.return_value = {
            "path": "vC_WAVESHARE/bridge/sphere_tools.py",
            "content": "tool source",
            "start_line": 1,
            "end_line": 1,
            "total_lines": 1,
            "truncated": False,
        }
        executor = SphereToolExecutor(
            memory_store=Mock(),
            source_store=source,
            action_queue=Mock(),
            origin_device_id="web-console",
            user_transcript="Show me your source code.",
        )

        listed = executor.execute(
            "source_list",
            {"path": "vC_WAVESHARE", "recursive": False, "limit": 50},
            call_id="call-list-source",
        )
        read = executor.execute(
            "source_read",
            {
                "path": "vC_WAVESHARE/bridge/sphere_tools.py",
                "start_line": 1,
                "line_count": 120,
            },
            call_id="call-read-source",
        )

        self.assertTrue(listed["ok"])
        self.assertEqual(read["file"]["content"], "tool source")
        source.list.assert_called_once_with("vC_WAVESHARE", recursive=False, limit=50)
        source.read.assert_called_once_with(
            "vC_WAVESHARE/bridge/sphere_tools.py",
            start_line=1,
            line_count=120,
        )

    def test_explicit_memory_write_preserves_device_provenance(self) -> None:
        memory = Mock()
        memory.remember.return_value = ({"id": "memory-1"}, True)
        executor = SphereToolExecutor(
            memory_store=memory,
            action_queue=Mock(),
            origin_device_id="wearabllm-android",
            user_transcript="Remember that I prefer green tea.",
        )
        result = executor.execute(
            "memory_remember",
            {
                "subject": "principal",
                "kind": "preference",
                "content": "The user prefers green tea.",
                "tags": ["tea"],
                "importance": 3,
                "expires_at": "",
            },
            call_id="call-memory",
        )
        self.assertTrue(result["created"])
        self.assertEqual(memory.remember.call_args.kwargs["source_device_id"], "wearabllm-android")

    def test_send_to_body_fans_out_one_expression_per_explicit_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = JsonActionQueue(Path(tmpdir) / "actions.json")
            executor = SphereToolExecutor(
                memory_store=None,
                action_queue=queue,
                origin_device_id="web-console",
                user_transcript="Send this to the phone and Waveshare.",
            )
            result = executor.execute(
                "send_to_body",
                {
                    "target_device_ids": ["wearabllm-android", "wearabllm-esp32"],
                    "text": "Dinner is ready.",
                    "command": "GC",
                    "channels": ["visual", "display", "audio"],
                    "expires_in_seconds": 300,
                },
                call_id="call-expression",
            )
            self.assertEqual(len(result["actions"]), 2)
            actions = queue.list(limit=10)
            self.assertEqual({action["target_device_id"] for action in actions}, {"wearabllm-android", "wearabllm-esp32"})
            self.assertEqual({action["expression"]["command"] for action in actions}, {"GC"})
            self.assertEqual(len({action["idempotency_key"] for action in actions}), 2)


if __name__ == "__main__":
    unittest.main()
