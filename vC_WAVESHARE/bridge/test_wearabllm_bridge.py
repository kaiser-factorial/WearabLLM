from __future__ import annotations

import json
import os
import struct
import tempfile
import threading
import unittest
import wave
from io import BytesIO
from argparse import Namespace
from http.client import HTTPConnection
from pathlib import Path
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from bridge_contracts import (
    GeneratedModelText,
    InteractionInput,
    InteractionResult,
    ModelActivity,
    QueryInput,
    QueryResult,
)
import wearabllm_bridge
from wearabllm_bridge import (
    BridgeState,
    DEFAULT_MAX_AUDIO_BYTES,
    inspect_wav,
    json_bytes,
    make_silence_wav,
    make_handler,
    markdown_to_plain_text,
    normalize_tts_wav,
    parse_command_sequence,
    parse_llm_response,
    pcm16_level_stats,
    TTS_INSTRUCTIONS,
)


class ParseLLMResponseTest(unittest.TestCase):
    def test_parses_expected_two_line_response(self):
        command, reply = parse_llm_response("GC\nYes, absolutely.")
        self.assertEqual(command, "GC")
        self.assertEqual(reply, "Yes, absolutely.")

    def test_parses_labeled_two_line_response(self):
        command, reply = parse_llm_response("LED: YP\nReply: Maybe, test it once more.")
        self.assertEqual(command, "YP")
        self.assertEqual(reply, "Maybe, test it once more.")

    def test_parses_json_response(self):
        command, reply = parse_llm_response('{"command":"PS","reply":"That is a good creative branch."}')
        self.assertEqual(command, "PS")
        self.assertEqual(reply, "That is a good creative branch.")

    def test_parses_fenced_json_response(self):
        command, reply = parse_llm_response('```json\n{"code":"RF","answer":"Stop and check power."}\n```')
        self.assertEqual(command, "RF")
        self.assertEqual(reply, "Stop and check power.")

    def test_parses_embedded_json_response(self):
        command, reply = parse_llm_response('Here is the result:\n{"command":"GP","reply":"Yes, keep going gently."}\nDone.')
        self.assertEqual(command, "GP")
        self.assertEqual(reply, "Yes, keep going gently.")

    def test_parses_embedded_json_with_brace_in_string(self):
        command, reply = parse_llm_response('Result: {"command":"BS","reply":"Use {braces} only in code."}')
        self.assertEqual(command, "BS")
        self.assertEqual(reply, "Use {braces} only in code.")

    def test_parses_embedded_command(self):
        command, reply = parse_llm_response("I would be careful here. LED: RF")
        self.assertEqual(command, "RF")
        self.assertIn("careful", reply)
        self.assertNotIn("LED:", reply)

    def test_falls_back_to_blue_solid_for_unparseable_response(self):
        command, reply = parse_llm_response("I do not know how to classify this.")
        self.assertEqual(command, "BS")
        self.assertEqual(reply, "I do not know how to classify this.")

    def test_empty_response_falls_back(self):
        command, reply = parse_llm_response("")
        self.assertEqual(command, "BS")
        self.assertEqual(reply, "")

    def test_preserves_markdown_structure_after_led_code(self):
        command, reply = parse_llm_response(
            "BS\n## Result\n\n- First item\n- **Second item**"
        )
        self.assertEqual(command, "BS")
        self.assertEqual(reply, "## Result\n\n- First item\n- **Second item**")

    def test_markdown_plain_text_projection_is_readable(self):
        self.assertEqual(
            markdown_to_plain_text("## Result\n\n- **Temperature:** `21.5 C`\n- [Details](https://example.com)"),
            "Result\n\n• Temperature: 21.5 C\n• Details",
        )


class BridgeStateTest(unittest.TestCase):
    def test_durable_memory_mutation_emits_content_free_audit(self):
        state = BridgeState.__new__(BridgeState)
        state.memory_store = Mock()
        state.memory_store.add.return_value = True
        state.openai_client = object()
        state.args = SimpleNamespace(memory_model="memory-model", llm_model="llm-model")
        state.policy = wearabllm_bridge.BridgePolicy()
        events: list[str] = []
        state.event_sink = events.append
        private_fact = "The user prefers jasmine tea every morning."
        state._generate_text = Mock(return_value=json.dumps([private_fact]))

        stored = state._extract_and_store_memory_payload({"user": private_fact})

        self.assertEqual(stored, 1)
        state.memory_store.add.assert_called_once_with(private_fact)
        rendered = "\n".join(events)
        self.assertIn('"operation":"memory_mutation"', rendered)
        self.assertIn('"outcome":"accepted"', rendered)
        self.assertNotIn(private_fact, rendered)

    def test_source_continuation_forces_source_read_tool(self):
        response = SimpleNamespace(id="resp-source", output=[], output_text="BS\nDone.")
        create = Mock(return_value=response)
        state = BridgeState.__new__(BridgeState)
        state.openai_client = SimpleNamespace(responses=SimpleNamespace(create=create))
        state.household_memory_store = None
        state.source_store = Mock()
        state.web_search_enabled = False
        state.max_tool_rounds = 8
        state.action_queue = Mock()
        state.pending_memory_confirmations = wearabllm_bridge.PendingMemoryConfirmationStore()

        state._generate_agent_text(
            "instructions",
            [{"role": "user", "content": "yes please, starting line 1201"}],
            max_output_tokens=256,
            model="gpt-5.4-mini",
            origin_device_id="web-console",
            user_transcript="yes please, starting line 1201",
        )

        self.assertEqual(
            create.call_args.kwargs["tool_choice"],
            {"type": "function", "name": "source_read"},
        )

    def test_malformed_function_arguments_return_bounded_tool_error(self):
        first = SimpleNamespace(
            id="resp-tool",
            output=[
                SimpleNamespace(
                    type="function_call",
                    name="memory_search",
                    call_id="call-tool",
                    arguments="{not-json",
                )
            ],
            output_text="",
        )
        second = SimpleNamespace(id="resp-final", output=[], output_text="BS\nI could not search memory.")
        create = Mock(side_effect=[first, second])
        state = BridgeState.__new__(BridgeState)
        state.openai_client = SimpleNamespace(responses=SimpleNamespace(create=create))
        state.household_memory_store = Mock()
        state.web_search_enabled = False
        state.max_tool_rounds = 4
        state.action_queue = Mock()
        state.pending_memory_confirmations = wearabllm_bridge.PendingMemoryConfirmationStore()

        raw, metadata = state._generate_agent_text(
            "instructions",
            [{"role": "user", "content": "Do you remember my preference?"}],
            max_output_tokens=256,
            model="gpt-5.4-mini",
            origin_device_id="web-console",
            user_transcript="Do you remember my preference?",
        )

        self.assertEqual(raw, "BS\nI could not search memory.")
        self.assertEqual(metadata["tool_results"][0]["name"], "memory_search")
        self.assertFalse(metadata["tool_results"][0]["ok"])
        self.assertEqual(create.call_count, 2)

    def test_tool_round_limit_returns_fallback_with_completed_activity(self):
        responses = [
            SimpleNamespace(
                id=f"resp-{index}",
                output=[
                    SimpleNamespace(
                        type="function_call",
                        name="memory_remember",
                        call_id=f"call-{index}",
                        arguments=json.dumps(
                            {
                                "subject": "principal",
                                "kind": "preference",
                                "content": f"Disposable preference {index}.",
                                "tags": ["test"],
                                "importance": 1,
                                "expires_at": "",
                            }
                        ),
                    )
                ],
                output_text="",
            )
            for index in range(5)
        ]
        create = Mock(side_effect=responses)
        memory = Mock()
        memory.remember.side_effect = [
            ({"id": f"memory-{index}", "content": f"Disposable preference {index}."}, True)
            for index in range(4)
        ]
        state = BridgeState.__new__(BridgeState)
        state.openai_client = SimpleNamespace(responses=SimpleNamespace(create=create))
        state.household_memory_store = memory
        state.web_search_enabled = False
        state.max_tool_rounds = 4
        state.action_queue = Mock()
        state.pending_memory_confirmations = wearabllm_bridge.PendingMemoryConfirmationStore()

        raw, metadata = state._generate_agent_text(
            "instructions",
            [{"role": "user", "content": "Remember several disposable preferences."}],
            max_output_tokens=256,
            model="gpt-5.4-mini",
            origin_device_id="web-console",
            user_transcript="Remember several disposable preferences.",
        )

        self.assertTrue(raw.startswith("RF\n"))
        self.assertIn("tool limit", raw)
        self.assertEqual(len(metadata["tool_results"]), 4)
        self.assertEqual(create.call_count, 5)

    def test_generation_failure_is_persisted_as_user_and_assistant_turns(self):
        state = BridgeState.__new__(BridgeState)
        state.args = Namespace(dry_run=False, provider="openai")
        state.openai_client = object()
        state.memory_store = None
        state.household_memory_store = Mock()
        state.memory_retrieval_limit = 3
        state.history_lock = threading.Lock()
        state.history = []
        state.history_turns = 10
        state.conversation_store = Mock()
        state.conversation_store.history.return_value = []
        state._prepare_active_session = Mock(return_value="session-1")
        state.current_agent_config = Mock(
            return_value=SimpleNamespace(system_prompt="Be helpful.", llm_model="gpt-5.4-mini")
        )
        state.max_output_tokens = 256
        state.conversation_backend = "supabase"
        state._generate_agent_result = Mock(side_effect=RuntimeError("provider unavailable"))

        command, reply, metadata = state.ask_llm_with_metadata(
            "Remember several things about me.",
            device_id="web-console",
        )

        self.assertEqual(command, "RF")
        self.assertIn("internal error", reply)
        self.assertEqual(metadata["sources"], [])
        self.assertEqual(metadata["tool_results"], [])
        self.assertEqual(metadata["persistence"]["status"], "persisted")
        self.assertEqual(state.conversation_store.append.call_count, 0)
        self.assertEqual(
            state.conversation_store.append_exchange.call_args,
            call(
                "session-1",
                "web-console",
                "Remember several things about me.",
                "web-console",
                reply,
                assistant_metadata=None,
            ),
        )

    def test_memory_mutation_withholds_web_search_even_after_prior_web_turn(self):
        first = SimpleNamespace(
            id="resp-memory",
            output=[
                SimpleNamespace(
                    type="function_call",
                    name="memory_remember",
                    call_id="call-memory",
                    arguments=json.dumps(
                        {
                            "subject": "principal",
                            "kind": "fact",
                            "content": "The amber key is kept in the north drawer.",
                            "tags": ["temporary-verification"],
                            "importance": 1,
                            "expires_at": "",
                        }
                    ),
                )
            ],
            output_text="",
        )
        second = SimpleNamespace(id="resp-final", output=[], output_text="GC\nRemembered.")
        create = Mock(side_effect=[first, second])
        memory = Mock()
        memory.remember.return_value = ({"id": "memory-1"}, True)
        state = BridgeState.__new__(BridgeState)
        state.openai_client = SimpleNamespace(responses=SimpleNamespace(create=create))
        state.household_memory_store = memory
        state.web_search_enabled = True
        state.max_tool_rounds = 4
        state.action_queue = Mock()

        _text, metadata = state._generate_agent_text(
            "instructions",
            [
                {"role": "user", "content": "Use web search for the OpenAI embeddings guide."},
                {"role": "assistant", "content": "The guide says 1536 dimensions by default."},
                {"role": "user", "content": "Remember that the amber key is in the north drawer."},
            ],
            max_output_tokens=256,
            model="gpt-5.4-mini",
            origin_device_id="web-console",
            user_transcript="Remember that the amber key is in the north drawer.",
        )

        self.assertEqual(metadata["sources"], [])
        for request in create.call_args_list:
            tool_types = [tool["type"] for tool in request.kwargs["tools"]]
            self.assertNotIn("web_search", tool_types)
        followup_names = {
            tool.get("name")
            for tool in create.call_args_list[1].kwargs["tools"]
            if tool.get("type") == "function"
        }
        self.assertTrue(followup_names)
        self.assertTrue(all(name.startswith("memory_") for name in followup_names))

    def test_memory_mutation_can_include_web_only_when_current_turn_explicitly_requests_both(self):
        create = Mock(return_value=SimpleNamespace(id="resp-final", output=[], output_text="BS\nDone."))
        state = BridgeState.__new__(BridgeState)
        state.openai_client = SimpleNamespace(responses=SimpleNamespace(create=create))
        state.household_memory_store = Mock()
        state.web_search_enabled = True
        state.max_tool_rounds = 4
        state.action_queue = Mock()

        state._generate_agent_text(
            "instructions",
            [{"role": "user", "content": "Search the web for the current result, then remember it."}],
            max_output_tokens=256,
            model="gpt-5.4-mini",
            origin_device_id="web-console",
            user_transcript="Search the web for the current result, then remember it.",
        )

        self.assertIn("web_search", [tool["type"] for tool in create.call_args.kwargs["tools"]])

    def test_safe_profile_statement_exposes_memory_without_eager_web_search(self):
        create = Mock(return_value=SimpleNamespace(id="resp-final", output=[], output_text="BS\nGot it."))
        state = BridgeState.__new__(BridgeState)
        state.openai_client = SimpleNamespace(responses=SimpleNamespace(create=create))
        state.household_memory_store = Mock()
        state.web_search_enabled = True
        state.max_tool_rounds = 4
        state.action_queue = Mock()
        state.pending_memory_confirmations = wearabllm_bridge.PendingMemoryConfirmationStore()

        state._generate_agent_text(
            "instructions",
            [{"role": "user", "content": "I prefer a curious, direct, playful tone."}],
            max_output_tokens=256,
            model="gpt-5.4-mini",
            origin_device_id="web-console",
            user_transcript="I prefer a curious, direct, playful tone.",
        )

        tools = create.call_args.kwargs["tools"]
        self.assertNotIn("web_search", [tool["type"] for tool in tools])
        self.assertIn("memory_remember", {tool.get("name") for tool in tools})

    def test_current_information_turn_exposes_web_search(self):
        create = Mock(return_value=SimpleNamespace(id="resp-final", output=[], output_text="BS\nChecking."))
        state = BridgeState.__new__(BridgeState)
        state.openai_client = SimpleNamespace(responses=SimpleNamespace(create=create))
        state.household_memory_store = Mock()
        state.web_search_enabled = True
        state.max_tool_rounds = 4
        state.action_queue = Mock()
        state.pending_memory_confirmations = wearabllm_bridge.PendingMemoryConfirmationStore()

        state._generate_agent_text(
            "instructions",
            [{"role": "user", "content": "What's the latest OpenAI model today?"}],
            max_output_tokens=256,
            model="gpt-5.4-mini",
            origin_device_id="web-console",
            user_transcript="What's the latest OpenAI model today?",
        )

        self.assertIn("web_search", [tool["type"] for tool in create.call_args.kwargs["tools"]])

    def test_pending_no_is_forced_through_memory_confirm_then_returns_to_auto(self):
        first = SimpleNamespace(
            id="resp-confirm",
            output=[
                SimpleNamespace(
                    type="function_call",
                    name="memory_confirm",
                    call_id="call-confirm",
                    arguments=json.dumps({"save": False}),
                )
            ],
            output_text="",
        )
        second = SimpleNamespace(id="resp-final", output=[], output_text="BS\nDiscarded.")
        create = Mock(side_effect=[first, second])
        pending = wearabllm_bridge.PendingMemoryConfirmationStore()
        pending.stage(
            {
                "subject": "principal",
                "kind": "fact",
                "content": "The user lives at 987 Test Lane.",
                "tags": [],
                "importance": 4,
                "expires_at": "",
            },
            source_device_id="web-console",
            sensitive_categories=["precise_address"],
        )
        state = BridgeState.__new__(BridgeState)
        state.openai_client = SimpleNamespace(responses=SimpleNamespace(create=create))
        state.household_memory_store = Mock()
        state.web_search_enabled = True
        state.max_tool_rounds = 4
        state.action_queue = Mock()
        state.pending_memory_confirmations = pending

        _text, metadata = state._generate_agent_text(
            "instructions",
            [{"role": "user", "content": "No, do not save it."}],
            max_output_tokens=256,
            model="gpt-5.4-mini",
            origin_device_id="web-console",
            user_transcript="No, do not save it.",
        )

        self.assertEqual(
            create.call_args_list[0].kwargs["tool_choice"],
            {"type": "function", "name": "memory_confirm"},
        )
        self.assertEqual(create.call_args_list[1].kwargs["tool_choice"], "auto")
        self.assertEqual(metadata["tool_results"][0]["saved"], False)
        self.assertFalse(pending.has_pending())

    def test_personal_address_claim_is_forced_through_staging_tool(self):
        first = SimpleNamespace(
            id="resp-stage",
            output=[
                SimpleNamespace(
                    type="function_call",
                    name="memory_remember",
                    call_id="call-stage",
                    arguments=json.dumps(
                        {
                            "subject": "principal",
                            "kind": "fact",
                            "content": "The user's home address is 985 Test Lane.",
                            "tags": ["address"],
                            "importance": 4,
                            "expires_at": "",
                        }
                    ),
                )
            ],
            output_text="",
        )
        second = SimpleNamespace(id="resp-final", output=[], output_text="BS\nShould I save it?")
        create = Mock(side_effect=[first, second])
        pending = wearabllm_bridge.PendingMemoryConfirmationStore()
        state = BridgeState.__new__(BridgeState)
        state.openai_client = SimpleNamespace(responses=SimpleNamespace(create=create))
        state.household_memory_store = Mock()
        state.web_search_enabled = True
        state.max_tool_rounds = 4
        state.action_queue = Mock()
        state.pending_memory_confirmations = pending

        _text, metadata = state._generate_agent_text(
            "instructions",
            [{"role": "user", "content": "My home address is 985 Test Lane."}],
            max_output_tokens=256,
            model="gpt-5.4-mini",
            origin_device_id="web-console",
            user_transcript="My home address is 985 Test Lane.",
        )

        self.assertEqual(
            create.call_args_list[0].kwargs["tool_choice"],
            {"type": "function", "name": "memory_remember"},
        )
        self.assertEqual(create.call_args_list[1].kwargs["tool_choice"], "auto")
        self.assertTrue(metadata["tool_results"][0]["confirmation_required"])
        self.assertTrue(pending.has_pending())

    def test_explicit_forget_with_id_is_forced_before_success_reply(self):
        memory_id = "6536beea-014a-48a8-b8af-43b0e8dc3cf2"
        first = SimpleNamespace(
            id="resp-forget",
            output=[
                SimpleNamespace(
                    type="function_call",
                    name="memory_forget",
                    call_id="call-forget",
                    arguments=json.dumps({"memory_id": memory_id}),
                )
            ],
            output_text="",
        )
        second = SimpleNamespace(id="resp-final", output=[], output_text="BS\nForgotten.")
        create = Mock(side_effect=[first, second])
        memory = Mock()
        memory.forget.return_value = {"id": memory_id, "status": "forgotten"}
        state = BridgeState.__new__(BridgeState)
        state.openai_client = SimpleNamespace(responses=SimpleNamespace(create=create))
        state.household_memory_store = memory
        state.web_search_enabled = True
        state.max_tool_rounds = 4
        state.action_queue = Mock()
        state.pending_memory_confirmations = wearabllm_bridge.PendingMemoryConfirmationStore()

        _text, metadata = state._generate_agent_text(
            "instructions",
            [{"role": "user", "content": f"Forget the memory with ID {memory_id}."}],
            max_output_tokens=256,
            model="gpt-5.4-mini",
            origin_device_id="web-console",
            user_transcript=f"Forget the memory with ID {memory_id}.",
        )

        self.assertEqual(
            create.call_args_list[0].kwargs["tool_choice"],
            {"type": "function", "name": "memory_forget"},
        )
        self.assertEqual(create.call_args_list[1].kwargs["tool_choice"], "auto")
        self.assertEqual(metadata["tool_results"][0]["memory_id"], memory_id)
        memory.forget.assert_called_once_with(memory_id)

    def test_new_conversation_ends_and_preserves_nonempty_active_session(self):
        store = Mock()
        active = {"id": "old-session", "title": "Saved discussion"}
        store.active_session.return_value = active
        store.turns.return_value = [{"id": 1, "role": "user", "content": "Keep this."}]
        store.create_session.return_value = {"id": "new-session"}
        state = BridgeState.__new__(BridgeState)
        state.conversation_store = store
        state.history = [{"role": "user", "content": "Keep this."}]
        state.history_lock = threading.Lock()

        result = state.start_new_conversation()

        store.end_session.assert_called_once_with(active)
        store.archive.assert_not_called()
        store.clear.assert_not_called()
        store.create_session.assert_called_once_with()
        self.assertEqual(result["ended_session_id"], "old-session")
        self.assertEqual(result["saved_turns"], 1)
        self.assertEqual(result["active_session_id"], "new-session")
        self.assertEqual(state.history, [])

    def test_new_conversation_does_not_create_another_empty_session(self):
        store = Mock()
        active = {"id": "empty-session"}
        store.active_session.return_value = active
        store.turns.return_value = []
        state = BridgeState.__new__(BridgeState)
        state.conversation_store = store
        state.history = []
        state.history_lock = threading.Lock()

        result = state.start_new_conversation()

        store.end_session.assert_not_called()
        store.create_session.assert_not_called()
        self.assertEqual(result["active_session_id"], "empty-session")
        self.assertEqual(result["saved_turns"], 0)

    def test_sphere_status_snapshot_is_sanitized_passive_observation(self):
        state = BridgeState.__new__(BridgeState)
        state.args = Namespace(provider="openai")
        state.device_presence = {
            "wearabllm-esp32": {"monotonic": 100.0, "last_seen_at": "2026-08-11T12:00:00Z"}
        }
        state.device_presence_lock = threading.Lock()
        state.conversation_backend = "supabase"
        state.action_backend = "supabase"
        state.household_memory_store = SimpleNamespace(
            hybrid_enabled=True,
            embedding_model="text-embedding-3-small",
        )
        state.web_search_enabled = True
        state.action_queue = Mock()
        state.action_queue.list.return_value = [
            {
                "id": "11111111-1111-4111-8111-111111111111",
                "target_device_id": "wearabllm-esp32",
                "command": "GC",
                "reply": "private spoken content",
                "transcript": "private user transcript",
                "status": "completed",
                "expression": {"channels": ["audio", "visual"], "text": "private"},
                "created_at": "2026-08-11T11:59:00Z",
                "updated_at": "2026-08-11T12:00:00Z",
                "error": None,
            }
        ]

        with patch("wearabllm_bridge.time.monotonic", return_value=110.0):
            snapshot = state.sphere_status_snapshot(
                ["wearabllm-esp32"],
                include_recent_actions=True,
            )

        self.assertEqual([body["id"] for body in snapshot["bodies"]], ["wearabllm-esp32"])
        self.assertTrue(snapshot["bodies"][0]["online"])
        self.assertEqual(snapshot["observation_kind"], "passive_control_plane")
        self.assertFalse(snapshot["physical_state_verified"])
        self.assertEqual(snapshot["recent_actions"][0]["command"], "GC")
        serialized = json.dumps(snapshot)
        for private_value in ("private spoken content", "private user transcript", '"text": "private"'):
            self.assertNotIn(private_value, serialized)
        self.assertNotIn("system_prompt", serialized)
        self.assertEqual(snapshot["services"]["memory"]["retrieval"], "hybrid")

    def test_sphere_status_snapshot_rejects_unknown_target_before_queue_read(self):
        state = BridgeState.__new__(BridgeState)
        state.action_queue = Mock()
        with self.assertRaises(ValueError):
            state.sphere_status_snapshot(["attacker-body"], include_recent_actions=True)
        state.action_queue.list.assert_not_called()

    def test_openai_agent_tool_loop_executes_function_and_returns_final_text(self):
        first = SimpleNamespace(
            id="resp-first",
            output=[
                SimpleNamespace(
                    type="function_call",
                    name="send_to_body",
                    call_id="call-send-1",
                    arguments=json.dumps(
                        {
                            "target_device_ids": ["wearabllm-esp32"],
                            "text": "Dinner is ready.",
                            "command": "GC",
                            "channels": ["visual", "display", "audio"],
                            "expires_in_seconds": 300,
                        }
                    ),
                )
            ],
            output_text="",
        )
        second = SimpleNamespace(id="resp-second", output=[], output_text="GC\nSent to Waveshare.")
        create = Mock(side_effect=[first, second])
        action_queue = Mock()
        action_queue.create.return_value = (
            {
                "id": "11111111-1111-4111-8111-111111111111",
                "target_device_id": "wearabllm-esp32",
                "status": "queued",
            },
            True,
        )
        state = BridgeState.__new__(BridgeState)
        state.openai_client = SimpleNamespace(responses=SimpleNamespace(create=create))
        state.household_memory_store = None
        state.web_search_enabled = True
        state.max_tool_rounds = 4
        state.action_queue = action_queue

        text, metadata = state._generate_agent_text(
            "instructions",
            [{"role": "user", "content": "Send this to Waveshare."}],
            max_output_tokens=256,
            model="gpt-5.4-mini",
            origin_device_id="web-console",
            user_transcript="Send this to Waveshare.",
        )

        self.assertEqual(text, "GC\nSent to Waveshare.")
        self.assertEqual(metadata["tool_results"][0]["name"], "send_to_body")
        self.assertEqual(
            metadata["tool_results"][0]["summary"],
            "Expression queued — wearabllm-esp32",
        )
        self.assertEqual(metadata["model_tool_context"][0]["name"], "send_to_body")
        self.assertIn("11111111-1111-4111-8111-111111111111", metadata["model_tool_context"][0]["output"])
        self.assertEqual(
            metadata["tool_results"][0]["action_ids"],
            ["11111111-1111-4111-8111-111111111111"],
        )
        self.assertNotIn("result", metadata["tool_results"][0])
        self.assertEqual(create.call_args_list[1].kwargs["previous_response_id"], "resp-first")
        self.assertEqual(create.call_args_list[1].kwargs["input"][0]["type"], "function_call_output")
        action_queue.create.assert_called_once()

    def test_public_tool_activity_includes_memory_content_prefix(self):
        summary = BridgeState._public_tool_result(
            "memory_remember",
            {
                "ok": True,
                "saved": True,
                "created": True,
                "memory": {
                    "id": "11111111-1111-4111-8111-111111111111",
                    "content": "Corina prefers curious, direct, playful replies with concrete evidence.",
                },
            },
            {"content": "Corina prefers curious, direct, playful replies with concrete evidence."},
        )

        self.assertEqual(
            summary["summary"],
            "Memory updated — Corina prefers curious, direct, playful replies with concrete evidence.",
        )
        self.assertNotIn("memory", summary)

    def test_public_tool_failure_hides_raw_backend_details(self):
        summary = BridgeState._public_tool_result(
            "memory_remember",
            {
                "ok": False,
                "error": (
                    'Supabase POST failed (400): {"details":"Failing row contains '
                    '(secret private content and internal record identifiers)"}'
                ),
            },
            {},
        )

        self.assertEqual(summary["summary"], "The private data backend rejected the request.")
        self.assertEqual(summary["error"], "The private data backend rejected the request.")
        self.assertNotIn("secret private content", json.dumps(summary))

    def test_builtin_web_search_gets_visible_tool_activity(self):
        response = SimpleNamespace(
            id="resp-web",
            output=[
                SimpleNamespace(
                    type="web_search_call",
                    action=SimpleNamespace(
                        sources=[SimpleNamespace(url="https://example.com", title="Example")]
                    ),
                )
            ],
            output_text="BS\nCurrent answer.",
        )
        create = Mock(return_value=response)
        state = BridgeState.__new__(BridgeState)
        state.openai_client = SimpleNamespace(responses=SimpleNamespace(create=create))
        state.household_memory_store = None
        state.source_store = None
        state.web_search_enabled = True
        state.max_tool_rounds = 4
        state.action_queue = Mock()

        _text, metadata = state._generate_agent_text(
            "instructions",
            [{"role": "user", "content": "What's the weather today?"}],
            max_output_tokens=256,
            model="gpt-5.4-mini",
            origin_device_id="web-console",
            user_transcript="What's the weather today?",
        )

        self.assertEqual(metadata["tool_results"][0]["name"], "web_search")
        self.assertEqual(metadata["tool_results"][0]["summary"], "Web searched — 1 source")

    def test_device_presence_expires_after_heartbeat_ttl(self):
        state = BridgeState.__new__(BridgeState)
        state.device_presence = {}
        state.device_presence_lock = threading.Lock()
        with patch("wearabllm_bridge.time.monotonic", side_effect=[100.0, 110.0, 121.0]):
            state.touch_device("web-console")
            self.assertTrue(state._presence_for("web-console")[0])
            self.assertFalse(state._presence_for("web-console")[0])

    def test_typed_transcript_bypasses_stt(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="hello from test",
                stt="openai",
                dry_run=False,
                dry_run_sequence="",
                save_wav_dir="",
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        self.assertEqual(state.transcribe(b"not audio"), "hello from test")

    def test_dry_run_audio_bypasses_stt(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                dry_run=True,
                dry_run_sequence="",
                save_wav_dir="",
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        self.assertIn("dry-run audio upload", state.transcribe(make_silence_wav(125)))

    def test_openai_transcription_uses_supported_upload_tuple(self):
        create = Mock(return_value="hello from microphone")
        state = BridgeState.__new__(BridgeState)
        state.args = Namespace(
            typed="",
            dry_run=False,
            stt="openai",
            stt_model="gpt-4o-transcribe",
        )
        state.openai_client = SimpleNamespace(
            audio=SimpleNamespace(
                transcriptions=SimpleNamespace(create=create),
            )
        )

        wav_bytes = make_silence_wav(125)
        self.assertEqual(state.transcribe(wav_bytes), "hello from microphone")
        upload = create.call_args.kwargs["file"]
        self.assertEqual(upload, ("wearabllm-capture.wav", wav_bytes, "audio/wav"))

    def test_save_debug_wav_writes_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = BridgeState(
                Namespace(
                    provider="openai",
                    typed="",
                    stt="openai",
                    dry_run=False,
                    dry_run_sequence="",
                    save_wav_dir=tmpdir,
                    max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
                )
            )
            path = state.save_debug_wav(b"RIFF-test")
            self.assertIsNotNone(path)
            assert path is not None
            self.assertEqual(Path(path).read_bytes(), b"RIFF-test")

    def test_answer_transcript_uses_shared_response_shape(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        result = state.answer_query(
            QueryInput(transcript="should I test the bridge?", audio_bytes=12)
        )
        self.assertIsInstance(result, QueryResult)
        payload = result.to_legacy_dict()
        self.assertEqual(payload["command"], "BS")
        self.assertEqual(payload["transcript"], "should I test the bridge?")
        self.assertEqual(payload["audio_bytes"], 12)
        self.assertIsNone(payload["saved_wav"])
        self.assertIsNone(payload["wav_info"])
        self.assertIn("Dry run transcript", payload["reply"])
        self.assertEqual(payload["persistence"]["status"], "skipped")

    def test_persistence_failure_is_explicit_in_reply_payload(self):
        state = BridgeState.__new__(BridgeState)
        state.args = Namespace(dry_run=False, provider="openai")
        state.openai_client = object()
        state.memory_store = None
        state.memory_retrieval_limit = 3
        state.history_lock = threading.Lock()
        state.history = []
        state.history_turns = 10
        state.conversation_backend = "supabase"
        state.conversation_store = Mock()
        state.conversation_store.history.return_value = []
        state.conversation_store.append_exchange.side_effect = RuntimeError(
            "conversation content violates 4000-character check"
        )
        state._prepare_active_session = Mock(return_value="session-1")
        state.current_agent_config = Mock(
            return_value=SimpleNamespace(system_prompt="Be helpful.", llm_model="test-model")
        )
        state.max_output_tokens = 1024
        state._generate_agent_result = Mock(
            return_value=GeneratedModelText(
                raw_text="BS\nA long but useful RFC.",
                activity=ModelActivity(),
            )
        )

        payload = state.answer_transcript("Please write the RFC.", device_id="web-console")

        self.assertEqual(payload["command"], "BS")
        self.assertEqual(payload["reply"], "A long but useful RFC.")
        self.assertEqual(payload["persistence"]["status"], "failed")
        self.assertEqual(
            payload["persistence"]["error_code"], "conversation_write_failed"
        )
        self.assertNotIn("4000-character", payload["persistence"]["message"])

    def test_response_body_is_recorded_separately_from_android_origin(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        state.answer_transcript(
            "Show this reply on the dashboard",
            device_id="wearabllm-android",
            response_device_id="web-console",
        )
        snapshot = state.conversation_snapshot()
        self.assertEqual(
            [turn["device_id"] for turn in snapshot["turns"]],
            ["wearabllm-android", "web-console"],
        )
        self.assertNotIn("local-bridge", {body["id"] for body in snapshot["devices"]})

    def test_legacy_local_bridge_history_is_exposed_as_web_console(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        state.history = [{"role": "assistant", "content": "legacy", "device_id": "local-bridge"}]
        snapshot = state.conversation_snapshot()
        self.assertEqual(snapshot["turns"][0]["device_id"], "web-console")
        self.assertNotIn("local-bridge", {body["id"] for body in snapshot["devices"]})

    def test_conversation_snapshot_hides_private_model_tool_context(self):
        state = BridgeState.__new__(BridgeState)
        state.conversation_store = Mock()
        state.conversation_store.active_session.return_value = {"id": "session-1"}
        state.conversation_store.list_sessions.return_value = [{"id": "session-1"}]
        state.conversation_store.turns.return_value = [
            {
                "id": 1,
                "device_id": "web-console",
                "role": "assistant",
                "content": "Done.",
                "metadata": {
                    "tool_results": [{"name": "source_read", "summary": "Source read"}],
                    "model_tool_context": [{"output": "private source body"}],
                },
            }
        ]
        state.conversation_store.list_device_ids.return_value = ["web-console"]
        state.conversation_backend = "supabase"
        state.device_presence = {}
        state.device_presence_lock = threading.Lock()
        snapshot = state.conversation_snapshot()
        metadata = snapshot["turns"][0]["metadata"]
        self.assertIn("tool_results", metadata)
        self.assertNotIn("model_tool_context", metadata)

    def test_answer_transcript_can_include_wav_info(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        wav_info = {"valid": True, "sample_rate": 16000}
        payload = state.answer_transcript("audio test", audio_bytes=44, wav_info=wav_info)
        self.assertEqual(payload["wav_info"], wav_info)

    def test_dry_run_tts_returns_wav(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                tts_model="test-model",
                tts_voice="alloy",
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        wav_bytes = state.synthesize_tts_wav("hello from tts")
        self.assertTrue(wav_bytes.startswith(b"RIFF"))
        with wave.open(BytesIO(wav_bytes), "rb") as wav_file:
            self.assertEqual(wav_file.getframerate(), 16000)
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.getsampwidth(), 2)
            self.assertGreater(wav_file.getnframes(), 0)

    def test_live_tts_uses_verse_and_theatrical_instructions(self):
        wav_bytes = make_silence_wav(125)
        create = Mock(return_value=SimpleNamespace(read=lambda: wav_bytes))
        state = BridgeState.__new__(BridgeState)
        state.args = Namespace(
            provider="openai",
            dry_run=False,
            tts_model="gpt-4o-mini-tts",
            tts_voice="verse",
            tts_instructions=TTS_INSTRUCTIONS,
        )
        state.openai_client = SimpleNamespace(
            audio=SimpleNamespace(speech=SimpleNamespace(create=create))
        )

        result = state.synthesize_tts_wav("The experiment is alive!")

        self.assertTrue(result.startswith(b"RIFF"))
        self.assertEqual(create.call_args.kwargs["voice"], "verse")
        self.assertEqual(create.call_args.kwargs["instructions"], TTS_INSTRUCTIONS)
        self.assertEqual(create.call_args.kwargs["response_format"], "wav")

    def test_normalize_tts_wav_resamples_and_rewrites_header(self):
        with BytesIO() as source:
            with wave.open(source, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(24000)
                wav_file.writeframes(struct.pack("<h", 1000) * 2400)
            normalized = normalize_tts_wav(source.getvalue())

        with wave.open(BytesIO(normalized), "rb") as wav_file:
            self.assertEqual(wav_file.getframerate(), 16000)
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.getsampwidth(), 2)
            self.assertEqual(wav_file.getnframes(), 1600)

    def test_runtime_config_reports_live_bridge_settings(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="bench test",
                stt="local-whisper",
                stt_model="gpt-4o-transcribe",
                llm_model="gpt-5.4-mini",
                tts_model="gpt-4o-mini-tts",
                tts_voice="alloy",
                save_wav_dir="./captures",
                allow_device_config=True,
                dry_run=True,
                dry_run_command="RF",
                dry_run_sequence="GS,RF",
                max_audio_bytes=123456,
            )
        )
        config = state.runtime_config()
        self.assertEqual(config["provider"], "openai")
        self.assertEqual(config["stt"], "local-whisper")
        self.assertEqual(config["llm_model"], "gpt-5.4-mini")
        self.assertTrue(config["dry_run"])
        self.assertEqual(config["dry_run_command"], "RF")
        self.assertEqual(config["dry_run_sequence"], ["GS", "RF"])
        self.assertTrue(config["device_config"])
        self.assertTrue(config["typed_bypass"])
        self.assertEqual(config["save_wav_dir"], "./captures")
        self.assertEqual(config["max_audio_bytes"], 123456)
        self.assertEqual(config["max_output_tokens"], 512)
        self.assertEqual(config["capture_count"], 0)
        self.assertIsNone(config["latest_capture"])
        self.assertIn("firmware_config", config)

    def test_openai_catalog_filters_live_models_for_the_picker(self):
        state = BridgeState.__new__(BridgeState)
        state.args = Namespace(provider="openai")
        state.openai_client = SimpleNamespace(
            models=SimpleNamespace(
                list=Mock(
                    return_value=SimpleNamespace(
                        data=[
                            SimpleNamespace(id="gpt-5.4-mini"),
                            SimpleNamespace(id="gpt-4o-mini"),
                            SimpleNamespace(id="gpt-4o-mini-tts"),
                            SimpleNamespace(id="tts-1-hd"),
                            SimpleNamespace(id="gpt-realtime-2"),
                            SimpleNamespace(id="gpt-image-1"),
                        ]
                    )
                )
            )
        )

        catalog = state.openai_catalog()

        self.assertEqual(catalog["source"], "live")
        self.assertEqual(catalog["assistant_models"], ["gpt-4o-mini", "gpt-5.4-mini"])
        self.assertEqual(catalog["tts_models"], ["gpt-4o-mini-tts", "tts-1-hd"])
        self.assertIn("marin", catalog["tts_voices"])
        self.assertIn("marin", catalog["tts_voices_by_model"]["gpt-4o-mini-tts"])
        self.assertNotIn("marin", catalog["tts_voices_by_model"]["tts-1-hd"])

    def test_runtime_config_reports_latest_audio_capture(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                stt_model="gpt-4o-transcribe",
                llm_model="gpt-5.4-mini",
                tts_model="gpt-4o-mini-tts",
                tts_voice="alloy",
                save_wav_dir="",
                allow_device_config=False,
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        state.record_capture(
            wav_bytes=32044,
            saved_wav=None,
            wav_info={"valid": True, "duration_ms": 1000, "appears_silent": False},
            transcript="hello hardware",
            command="GS",
        )
        config = state.runtime_config()
        self.assertEqual(config["capture_count"], 0)
        latest = config["latest_capture"]
        self.assertIsInstance(latest, dict)
        self.assertEqual(latest["audio_bytes"], 32044)
        self.assertEqual(latest["transcript_len"], len("hello hardware"))
        self.assertEqual(latest["command"], "GS")
        self.assertEqual(latest["wav_info"]["duration_ms"], 1000)

    def test_firmware_config_status_reads_config_helper_json(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                allow_device_config=True,
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        with tempfile.NamedTemporaryFile() as helper:
            helper_path = Path(helper.name)
            with patch.object(wearabllm_bridge, "CONFIGURE_FIRMWARE", helper_path), patch(
                "wearabllm_bridge.subprocess.run"
            ) as run:
                run.return_value.returncode = 0
                run.return_value.stdout = json.dumps(
                    {
                        "ready": True,
                        "wifi_ssid_set": True,
                        "led_self_test": True,
                        "display_enabled": True,
                        "display_self_test": False,
                    }
                )
                run.return_value.stderr = ""

                payload = state.firmware_config_status()

        args = run.call_args.args[0]
        self.assertEqual(args, [str(helper_path), "--status-json"])
        self.assertTrue(payload["available"])
        self.assertTrue(payload["ready"])
        self.assertTrue(payload["led_self_test"])
        self.assertTrue(payload["display_enabled"])
        self.assertFalse(payload["display_self_test"])

    def test_device_wifi_config_is_opt_in(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                allow_device_config=False,
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        with self.assertRaises(PermissionError):
            state.configure_device_wifi("ssid", "password")

    def test_device_wifi_config_requires_ssid_and_password_when_enabled(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                allow_device_config=True,
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        with self.assertRaises(ValueError):
            state.configure_device_wifi("", "password", "02:00:00:00:00:01")

    def test_device_config_validates_ptt_options(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                allow_device_config=True,
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        with self.assertRaises(ValueError):
            state.configure_device_wifi("ssid", "password", ptt_active_level=2)
        with self.assertRaises(ValueError):
            state.configure_device_wifi("ssid", "password", ptt_debounce_ms=251)
        with self.assertRaises(ValueError):
            state.configure_device_wifi("ssid", "password", ptt_pull="sideways")
        with self.assertRaises(ValueError):
            state.configure_device_wifi("ssid", "password", audio_out_volume=101)
        with self.assertRaises(ValueError):
            state.configure_device_wifi("ssid", "password", tts_max_bytes=4095)

    def test_optional_bool_parses_display_flags(self):
        self.assertIsNone(wearabllm_bridge.optional_bool(None))
        self.assertIsNone(wearabllm_bridge.optional_bool(""))
        self.assertTrue(wearabllm_bridge.optional_bool(True))
        self.assertTrue(wearabllm_bridge.optional_bool("yes"))
        self.assertFalse(wearabllm_bridge.optional_bool(False))
        self.assertFalse(wearabllm_bridge.optional_bool("off"))
        with self.assertRaises(ValueError):
            wearabllm_bridge.optional_bool("maybe")

    def test_device_config_forwards_hardware_options_to_configure_helper(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                allow_device_config=True,
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        with tempfile.NamedTemporaryFile() as helper:
            helper_path = Path(helper.name)
            with patch.object(wearabllm_bridge, "CONFIGURE_FIRMWARE", helper_path), patch(
                "wearabllm_bridge.subprocess.run"
            ) as run:
                run.return_value.returncode = 0
                run.return_value.stdout = ""
                run.return_value.stderr = ""

                payload = state.configure_device_wifi(
                    "ssid",
                    "password",
                    "02:00:00:00:00:01",
                    ptt_gpio=8,
                    ptt_active_level=1,
                    ptt_debounce_ms=45,
                    ptt_pull="down",
                    audio_out_enabled=True,
                    audio_out_volume=55,
                    tts_enabled=True,
                    tts_max_bytes=65536,
                    led_self_test=True,
                    display_enabled=True,
                    display_self_test=True,
                )

        args = run.call_args.args[0]
        self.assertEqual(args[0], str(helper_path))
        self.assertIn("--ptt-gpio", args)
        self.assertIn("--ptt-active-level", args)
        self.assertIn("--ptt-debounce-ms", args)
        self.assertIn("--ptt-pull", args)
        self.assertIn("--enable-audio-out", args)
        self.assertIn("--audio-out-volume", args)
        self.assertIn("--enable-tts", args)
        self.assertIn("--tts-max-bytes", args)
        self.assertIn("--enable-led-self-test", args)
        self.assertIn("--enable-display", args)
        self.assertIn("--enable-display-self-test", args)
        self.assertEqual(payload["ptt_gpio"], 8)
        self.assertEqual(payload["ptt_active_level"], 1)
        self.assertEqual(payload["ptt_debounce_ms"], 45)
        self.assertEqual(payload["ptt_pull"], "down")
        self.assertIs(payload["audio_out_enabled"], True)
        self.assertEqual(payload["audio_out_volume"], 55)
        self.assertIs(payload["tts_enabled"], True)
        self.assertEqual(payload["tts_max_bytes"], 65536)
        self.assertIs(payload["led_self_test"], True)
        self.assertIs(payload["display_enabled"], True)
        self.assertIs(payload["display_self_test"], True)

    def test_device_config_forwards_display_disable_options(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                allow_device_config=True,
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        with tempfile.NamedTemporaryFile() as helper:
            helper_path = Path(helper.name)
            with patch.object(wearabllm_bridge, "CONFIGURE_FIRMWARE", helper_path), patch(
                "wearabllm_bridge.subprocess.run"
            ) as run:
                run.return_value.returncode = 0
                run.return_value.stdout = ""
                run.return_value.stderr = ""

                payload = state.configure_device_wifi(
                    "ssid",
                    "password",
                    audio_out_enabled=False,
                    tts_enabled=False,
                    led_self_test=False,
                    display_enabled=False,
                    display_self_test=False,
                )

        args = run.call_args.args[0]
        self.assertIn("--disable-audio-out", args)
        self.assertIn("--disable-tts", args)
        self.assertIn("--disable-led-self-test", args)
        self.assertIn("--disable-display", args)
        self.assertIn("--disable-display-self-test", args)
        self.assertIs(payload["audio_out_enabled"], False)
        self.assertIs(payload["tts_enabled"], False)
        self.assertIs(payload["led_self_test"], False)
        self.assertIs(payload["display_enabled"], False)
        self.assertIs(payload["display_self_test"], False)

    def test_dry_run_command_override_controls_response_code(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="GC",
                dry_run_sequence="",
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        command, reply = state.ask_llm("test command override")
        self.assertEqual(command, "GC")
        self.assertIn("test command override", reply)

    def test_invalid_dry_run_command_is_rejected(self):
        with self.assertRaises(ValueError):
            BridgeState(
                Namespace(
                    provider="openai",
                    typed="",
                    stt="openai",
                    save_wav_dir="",
                    dry_run=True,
                    dry_run_command="NO",
                    dry_run_sequence="",
                    max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
                )
            )

    def test_dry_run_sequence_cycles_response_codes(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="GS, RF",
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        self.assertEqual(state.ask_llm("first")[0], "GS")
        self.assertEqual(state.ask_llm("second")[0], "RF")
        self.assertEqual(state.ask_llm("third")[0], "GS")

    def test_live_llm_retains_bounded_session_history(self):
        create = Mock(
            side_effect=[
                SimpleNamespace(output_text="BS\nMy name is WearabLLM."),
                SimpleNamespace(output_text="GP\nYes, I remember."),
            ]
        )
        with patch("wearabllm_bridge.OpenAI") as openai:
            openai.return_value = SimpleNamespace(
                responses=SimpleNamespace(create=create)
            )
            state = BridgeState(
                Namespace(
                    provider="openai", typed="", stt="openai", save_wav_dir="",
                    dry_run=False, dry_run_command="BS", dry_run_sequence="",
                    history_turns=1, llm_model="test-model",
                    max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
                )
            )

        state.ask_llm("What is your name?")
        state.ask_llm("Do you remember?")
        self.assertEqual(create.call_args_list[0].kwargs["max_output_tokens"], 512)
        second_input = create.call_args_list[1].kwargs["input"]
        self.assertEqual([message["role"] for message in second_input], ["user", "assistant", "user"])
        self.assertEqual(len(state.history), 2)
        state.clear_history()
        self.assertEqual(state.history, [])

    def test_live_llm_extracts_and_retrieves_durable_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_file = Path(temp_dir) / "memory.json"
            create = Mock(
                side_effect=[
                    SimpleNamespace(output_text="GP\nTea sounds right."),
                    SimpleNamespace(output_text='["The user prefers green tea in the morning."]'),
                    SimpleNamespace(output_text="BS\nYou prefer green tea."),
                    SimpleNamespace(output_text="[]"),
                ]
            )
            with patch("wearabllm_bridge.OpenAI") as openai:
                openai.return_value = SimpleNamespace(
                    responses=SimpleNamespace(create=create)
                )
                state = BridgeState(
                    Namespace(
                        provider="openai", typed="", stt="openai", save_wav_dir="",
                        dry_run=False, dry_run_command="BS", dry_run_sequence="",
                        history_turns=0, llm_model="test-model", memory_model="test-model",
                        durable_memory=True, memory_file=str(memory_file),
                        memory_retrieval_limit=3,
                        max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
                    )
                )

            state.ask_llm("I prefer green tea in the morning.")
            self.assertEqual(len(state.memory_store.list()), 1)
            state.ask_llm("What tea do I prefer?")
            self.assertIn(
                "The user prefers green tea in the morning.",
                create.call_args_list[2].kwargs["instructions"],
            )

    @patch("wearabllm_bridge.SupabaseConversationStore.from_environment")
    def test_live_llm_uses_and_persists_shared_conversation(self, store_factory):
        store = Mock()
        store.active_session.return_value = {"id": "session-1", "last_turn_at": "2099-01-01T00:00:00Z"}
        store.session_expired.return_value = False
        store.history.return_value = [{"role": "assistant", "content": "Earlier shared reply."}]
        store_factory.return_value = store
        create = Mock(return_value=SimpleNamespace(output_text="BS\nCurrent shared reply."))
        with patch("wearabllm_bridge.OpenAI") as openai:
            openai.return_value = SimpleNamespace(responses=SimpleNamespace(create=create))
            state = BridgeState(
                Namespace(
                    provider="openai", typed="", stt="openai", save_wav_dir="",
                    dry_run=False, dry_run_command="BS", dry_run_sequence="",
                    history_turns=2, llm_model="test-model", max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
                    conversation_backend="supabase",
                )
            )

        state.ask_llm("What did we discuss?", device_id="wearabllm-esp32")
        self.assertEqual(
            create.call_args.kwargs["input"],
            [
                {"role": "assistant", "content": "Earlier shared reply."},
                {"role": "user", "content": "What did we discuss?"},
            ],
        )
        store.append_exchange.assert_called_once_with(
            "session-1",
            "wearabllm-esp32",
            "What did we discuss?",
            "wearabllm-esp32",
            "Current shared reply.",
            assistant_metadata=None,
        )
        state.clear_history()
        store.clear.assert_called_once_with()

    @patch.dict(os.environ, {"OPENROUTER_API_KEY": "openrouter-test"}, clear=False)
    @patch("wearabllm_bridge.OpenAI")
    def test_openrouter_uses_chat_completions_with_compatible_client(self, openai):
        create = Mock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="BS\nRouter reply."))]
        ))
        openai.return_value = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        state = BridgeState(
            Namespace(
                provider="openrouter", typed="", stt="openrouter", save_wav_dir="",
                dry_run=False, dry_run_command="BS", dry_run_sequence="",
                history_turns=0, llm_model="router/model", max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )

        self.assertEqual(state.ask_llm("Hello")[1], "Router reply.")
        openai.assert_called_once_with(
            api_key="openrouter-test",
            base_url="https://openrouter.ai/api/v1",
        )
        self.assertEqual(create.call_args.kwargs["model"], "router/model")
        self.assertEqual(create.call_args.kwargs["messages"][-1], {"role": "user", "content": "Hello"})

    @patch("wearabllm_bridge.SupabaseConversationStore.from_environment")
    def test_expired_session_is_summarized_archived_and_replaced(self, store_factory):
        store = Mock()
        store.active_session.return_value = {"id": "old-session", "last_turn_at": "2026-01-01T00:00:00Z"}
        store.session_expired.return_value = True
        store.turns.return_value = [{"role": "user", "content": "Old private conversation."}]
        store.create_session.return_value = {"id": "new-session"}
        store.history.return_value = []
        store_factory.return_value = store
        create = Mock(side_effect=[
            SimpleNamespace(output_text="The user was planning a move."),
            SimpleNamespace(output_text="BS\nFresh session reply."),
        ])
        with patch("wearabllm_bridge.OpenAI") as openai:
            openai.return_value = SimpleNamespace(responses=SimpleNamespace(create=create))
            state = BridgeState(
                Namespace(
                    provider="openai", typed="", stt="openai", save_wav_dir="",
                    dry_run=False, dry_run_command="BS", dry_run_sequence="",
                    history_turns=2, llm_model="test-model", max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
                    conversation_backend="supabase", session_idle_seconds=3600,
                )
            )

        self.assertEqual(state.ask_llm("What should we do next?")[1], "Fresh session reply.")
        store.archive.assert_called_once_with({"id": "old-session", "last_turn_at": "2026-01-01T00:00:00Z"}, "The user was planning a move.")
        store.append_exchange.assert_called_once_with(
            "new-session",
            "wearabllm-unknown",
            "What should we do next?",
            "wearabllm-unknown",
            "Fresh session reply.",
            assistant_metadata=None,
        )

    def test_parse_command_sequence_rejects_unknown_code(self):
        with self.assertRaises(ValueError):
            parse_command_sequence("GS,NOPE")

    def test_parse_command_sequence_allows_spacing_and_lowercase(self):
        self.assertEqual(parse_command_sequence(" gs, pp "), ["GS", "PP"])


class BridgeHandlerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def make_state(self, device_token: str = "") -> BridgeState:
        return BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                stt_model="gpt-4o-transcribe",
                llm_model="gpt-5.4-mini",
                tts_model="gpt-4o-mini-tts",
                tts_voice="alloy",
                save_wav_dir="",
                allow_device_config=False,
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                max_audio_bytes=96,
                device_token=device_token,
                action_queue_file=str(Path(self.temp_dir.name) / "actions.json"),
                agent_config_file=str(Path(self.temp_dir.name) / "agent_config.json"),
            )
        )

    def request(self, method: str, path: str, body: bytes = b"", headers: dict[str, str] | None = None, device_token: str = "") -> tuple[int, dict[str, object]]:
        handler = make_handler(self.make_state(device_token), event_sink=lambda _line: None)
        handler.log_message = lambda *_args: None  # type: ignore[method-assign]
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            raw = response.read()
            conn.close()
            return response.status, json.loads(raw.decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_missing_transcript_returns_json_error(self):
        status, payload = self.request(
            "POST",
            "/v1/query_text",
            body=b'{"transcript":""}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "Missing transcript")

    def test_query_response_reports_non_durable_dry_run(self):
        status, payload = self.request(
            "POST",
            "/v1/query_text",
            body=b'{"transcript":"hello"}',
            headers={"Content-Type": "application/json"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["persistence"]["status"], "skipped")
        self.assertIn("dry-run", str(payload["persistence"]["message"]))

    def test_unknown_endpoint_returns_json_error(self):
        status, payload = self.request("GET", "/not-found")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "Unknown endpoint")

    def test_oversized_audio_upload_returns_json_error(self):
        status, payload = self.request(
            "POST",
            "/v1/query",
            body=b"0" * 97,
            headers={"Content-Type": "audio/wav"},
        )
        self.assertEqual(status, 413)
        self.assertIn("Audio body too large", str(payload["error"]))

    def test_device_token_protects_post_endpoints(self):
        status, payload = self.request(
            "POST",
            "/v1/query_text",
            body=b'{"transcript":"hello"}',
            headers={"Content-Type": "application/json"},
            device_token="test-token",
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "Invalid or missing device token")

        status, payload = self.request(
            "POST",
            "/v1/query_text",
            body=b'{"transcript":"hello"}',
            headers={
                "Content-Type": "application/json",
                "X-WearabLLM-Device-Token": "test-token",
            },
            device_token="test-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["command"], "BS")

    def test_invalid_device_id_returns_json_error(self):
        status, payload = self.request(
            "POST",
            "/v1/query_text",
            body=b'{"transcript":"hello"}',
            headers={
                "Content-Type": "application/json",
                "X-WearabLLM-Device-Id": "not valid",
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "Invalid device ID")

    def test_conversation_snapshot_endpoint_is_token_protected(self):
        status, payload = self.request(
            "GET",
            "/v1/conversation",
            device_token="console-token",
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "Invalid or missing device token")

        status, payload = self.request(
            "GET",
            "/v1/conversation",
            headers={"X-WearabLLM-Device-Token": "console-token"},
            device_token="console-token",
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertIn("turns", payload)
        self.assertIn("devices", payload)
        device_ids = {item["id"] for item in payload["devices"]}
        self.assertIn("wearabllm-esp32", device_ids)
        self.assertIn("wearabllm-android", device_ids)
        self.assertIn("web-console", device_ids)
        self.assertIn("wearabllm-wearable", device_ids)

    def test_devices_endpoint_lists_known_bodies(self):
        status, payload = self.request(
            "GET",
            "/v1/devices",
            headers={"X-WearabLLM-Device-Token": "console-token"},
            device_token="console-token",
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(len(payload["devices"]), 4)
        devices = {item["id"]: item for item in payload["devices"]}
        self.assertEqual(devices["wearabllm-esp32"]["label"], "Waveshare")
        self.assertEqual(devices["wearabllm-android"]["label"], "Android")
        self.assertNotIn("local-bridge", devices)

    def test_invalid_response_device_id_returns_json_error(self):
        status, payload = self.request(
            "POST",
            "/v1/query_text",
            body=b'{"transcript":"hello","response_device_id":"not valid"}',
            headers={
                "Content-Type": "application/json",
                "X-WearabLLM-Device-Id": "wearabllm-android",
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "Invalid device ID")

    def test_targeted_interaction_is_claimed_and_acknowledged_by_board(self):
        status, created = self.request(
            "POST",
            "/v1/interactions",
            body=json.dumps(
                {
                    "transcript": "Tell the roomies dinner is ready.",
                    "origin_device_id": "wearabllm-android",
                    "target_device_id": "wearabllm-esp32",
                    "idempotency_key": "phone-dinner-1",
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        action = created["action"]
        assert isinstance(action, dict)
        self.assertEqual(action["status"], "queued")
        action_id = str(action["id"])

        status, claimed = self.request(
            "GET",
            "/v1/devices/wearabllm-esp32/actions",
            headers={"X-WearabLLM-Device-Id": "wearabllm-esp32"},
        )
        self.assertEqual(status, 200)
        claimed_action = claimed["action"]
        assert isinstance(claimed_action, dict)
        self.assertEqual(claimed_action["id"], action_id)
        self.assertEqual(claimed_action["status"], "dispatched")

        status, acknowledged = self.request(
            "POST",
            f"/v1/devices/wearabllm-esp32/actions/{action_id}/ack",
            body=b'{"status":"played"}',
            headers={
                "Content-Type": "application/json",
                "X-WearabLLM-Device-Id": "wearabllm-esp32",
            },
        )
        self.assertEqual(status, 200)
        final_action = acknowledged["action"]
        assert isinstance(final_action, dict)
        self.assertEqual(final_action["status"], "played")

    def test_interaction_requires_matching_board_identity_to_claim(self):
        status, _created = self.request(
            "POST",
            "/v1/interactions",
            body=b'{"transcript":"hello","origin_device_id":"wearabllm-android","target_device_id":"wearabllm-esp32"}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        status, payload = self.request(
            "GET",
            "/v1/devices/wearabllm-esp32/actions",
            headers={"X-WearabLLM-Device-Id": "wearabllm-android"},
        )
        self.assertEqual(status, 403)
        self.assertIn("does not match", str(payload["error"]))


class StartupPrivacyTest(unittest.TestCase):
    def test_hosted_bridge_rejects_content_logging(self):
        argv = ["wearabllm_bridge.py", "--dry-run", "--debug-content-logs"]
        environment = {
            "WEARABLLM_HOSTED": "1",
            "WEARABLLM_DEVICE_TOKEN": "test-token",
        }
        with (
            patch.object(wearabllm_bridge.sys, "argv", argv),
            patch.dict(os.environ, environment, clear=False),
            self.assertRaisesRegex(SystemExit, "local-only"),
        ):
            wearabllm_bridge.main()


class TargetedInteractionStateTest(unittest.TestCase):
    def test_phone_prompt_creates_persistent_board_action(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = BridgeState(
                Namespace(
                    provider="openai",
                    typed="",
                    stt="openai",
                    save_wav_dir="",
                    dry_run=True,
                    dry_run_command="GP",
                    dry_run_sequence="",
                    max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
                    action_queue_file=str(Path(tmpdir) / "actions.json"),
                    agent_config_file=str(Path(tmpdir) / "agent_config.json"),
                )
            )
            result = state.create_interaction_result(
                InteractionInput(
                    transcript="Tell the roomies that dinner is ready.",
                    origin_device_id="wearabllm-android",
                    target_device_id="wearabllm-esp32",
                    idempotency_key="dinner-ready-1",
                )
            )
            self.assertIsInstance(result, InteractionResult)
            created = result.to_legacy_dict()
            action = created["action"]
            self.assertTrue(created["action_created"])
            self.assertEqual(action["command"], "GP")
            self.assertEqual(action["status"], "queued")

            claimed = state.action_queue.claim_next("wearabllm-esp32")
            assert claimed is not None
            self.assertEqual(claimed["id"], action["id"])
            played = state.action_queue.acknowledge("wearabllm-esp32", str(action["id"]), "played")
            self.assertEqual(played["status"], "played")


class JsonBytesTest(unittest.TestCase):
    def test_json_bytes_is_ascii_json(self):
        payload = {"command": "PS", "reply": "cool idea"}
        encoded = json_bytes(payload)
        self.assertEqual(json.loads(encoded.decode("ascii")), payload)


class WavTest(unittest.TestCase):
    def test_make_silence_wav_is_valid_16khz_mono(self):
        wav_bytes = make_silence_wav(500)
        self.assertTrue(wav_bytes.startswith(b"RIFF"))
        with wave.open(BytesIO(wav_bytes), "rb") as wav_file:
            self.assertEqual(wav_file.getframerate(), 16000)
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.getsampwidth(), 2)
            self.assertEqual(wav_file.getnframes(), 8000)

    def test_inspect_wav_reports_expected_metadata(self):
        wav_bytes = make_silence_wav(250)
        info = inspect_wav(wav_bytes)
        self.assertTrue(info["valid"])
        self.assertEqual(info["sample_rate"], 16000)
        self.assertEqual(info["channels"], 1)
        self.assertEqual(info["sample_width_bytes"], 2)
        self.assertEqual(info["frames"], 4000)
        self.assertEqual(info["duration_ms"], 250)
        self.assertTrue(info["appears_silent"])
        self.assertEqual(info["peak_abs"], 0)
        self.assertIsNone(info["rms_dbfs"])

    def test_pcm16_level_stats_reports_non_silent_audio(self):
        pcm = struct.pack("<hhhh", 0, 1200, -1200, 0)
        stats = pcm16_level_stats(pcm)
        self.assertEqual(stats["peak_abs"], 1200)
        self.assertFalse(stats["appears_silent"])
        self.assertLess(stats["peak_dbfs"], 0)
        self.assertLess(stats["rms_dbfs"], 0)

    def test_inspect_wav_reports_non_silent_metadata(self):
        with BytesIO() as buffer:
            with wave.open(buffer, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(struct.pack("<hhhh", 0, 1200, -1200, 0))
            info = inspect_wav(buffer.getvalue())
        self.assertTrue(info["valid"])
        self.assertFalse(info["appears_silent"])
        self.assertEqual(info["peak_abs"], 1200)

    def test_inspect_wav_reports_invalid_audio(self):
        info = inspect_wav(b"not a wav")
        self.assertFalse(info["valid"])
        self.assertIn("error", info)


if __name__ == "__main__":
    unittest.main()
