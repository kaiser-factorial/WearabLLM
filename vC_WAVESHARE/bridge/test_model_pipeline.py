from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from bridge_policy import BridgePolicy
from model_pipeline import (
    FOLLOWUP_FAILURE_TEXT,
    INITIAL_FAILURE_TEXT,
    ROUND_LIMIT_TEXT,
    ModelRequestContext,
    ModelToolPipeline,
    ToolTurnPlan,
    build_model_request_context,
    build_tool_turn_plan,
)


def tool_response(index: int, *, name: str = "example_tool", arguments: str = "{}"):
    return SimpleNamespace(
        id=f"resp-{index}",
        output=[
            SimpleNamespace(
                type="function_call",
                name=name,
                call_id=f"call-{index}",
                arguments=arguments,
            )
        ],
        output_text="",
    )


def terminal_response(text: str = "BS\nDone."):
    return SimpleNamespace(id="resp-terminal", output=[], output_text=text)


class ModelToolPipelineTest(unittest.TestCase):
    def make_context(self) -> ModelRequestContext:
        return ModelRequestContext(
            instructions="instructions",
            input_messages=({"role": "user", "content": "hello"},),
            model="model",
            max_output_tokens=256,
        )

    def make_plan(self, *, forced: bool = False) -> ToolTurnPlan:
        return ToolTurnPlan(
            tools=({"type": "function", "name": "example_tool"},),
            tool_choice=(
                {"type": "function", "name": "example_tool"} if forced else None
            ),
            web_search_enabled=False,
            reset_tool_choice_after_first_response=forced,
        )

    def test_context_builder_normalizes_history_and_scopes_memory(self) -> None:
        context = build_model_request_context(
            system_instructions="system",
            history_messages=[
                {"role": "assistant", "content": "Earlier"},
                {"content": 42},
            ],
            user_transcript="Now",
            memories=["prefers tea", ""],
            model="model",
            max_output_tokens=256,
            tool_instructions="\nTOOLS",
        )

        self.assertEqual(
            context.input_messages,
            (
                {"role": "assistant", "content": "Earlier"},
                {"role": "user", "content": "42"},
                {"role": "user", "content": "Now"},
            ),
        )
        self.assertIn("potentially stale", context.instructions)
        self.assertIn("- prefers tea", context.instructions)
        self.assertTrue(context.instructions.endswith("\nTOOLS"))

    def test_previous_response_loop_executes_tool_and_reaches_terminal_reply(self) -> None:
        create = Mock(side_effect=[tool_response(1), terminal_response("GC\nFinished.")])
        execute = Mock(return_value={"ok": True, "value": "private model result"})
        pipeline = ModelToolPipeline(
            response_create=create,
            tool_execute=execute,
            max_tool_rounds=4,
            emit_exception=Mock(),
            emit_round_limit=Mock(),
        )

        result = pipeline.run(self.make_context(), self.make_plan(forced=True))

        self.assertEqual(result.raw_text, "GC\nFinished.")
        self.assertEqual(create.call_args_list[1].kwargs["previous_response_id"], "resp-1")
        self.assertEqual(create.call_args_list[1].kwargs["tool_choice"], "auto")
        output = json.loads(create.call_args_list[1].kwargs["input"][0]["output"])
        self.assertEqual(output["value"], "private model result")
        self.assertEqual(result.activity.tool_results[0].summary, "Tool used — example_tool")

    def test_malformed_arguments_become_bounded_public_tool_failure(self) -> None:
        create = Mock(
            side_effect=[
                tool_response(1, name="memory_search", arguments="{not-json"),
                terminal_response(),
            ]
        )
        execute = Mock()
        pipeline = ModelToolPipeline(
            response_create=create,
            tool_execute=execute,
            max_tool_rounds=4,
            emit_exception=Mock(),
            emit_round_limit=Mock(),
        )

        result = pipeline.run(self.make_context(), self.make_plan())

        execute.assert_not_called()
        self.assertFalse(result.activity.tool_results[0].ok)
        self.assertEqual(result.activity.tool_results[0].summary, "memory_search failed.")
        self.assertNotIn(
            "not-json",
            json.dumps(result.activity.tool_results[0].to_legacy_dict()),
        )

    def test_tool_exception_is_private_to_model_context_and_publicly_sanitized(self) -> None:
        private_error = "Supabase rejected private memory content"
        create = Mock(side_effect=[tool_response(1, name="memory_remember"), terminal_response()])
        pipeline = ModelToolPipeline(
            response_create=create,
            tool_execute=Mock(side_effect=RuntimeError(private_error)),
            max_tool_rounds=4,
            emit_exception=Mock(),
            emit_round_limit=Mock(),
        )

        result = pipeline.run(self.make_context(), self.make_plan())

        public = result.activity.tool_results[0].to_legacy_dict()
        self.assertNotIn(private_error, json.dumps(public))
        self.assertEqual(public["summary"], "The private data backend rejected the request.")
        self.assertIn(private_error, result.activity.model_tool_context[0].output)

    def test_unknown_tool_failure_is_bounded_and_publicly_sanitized(self) -> None:
        private_error = "Unknown tool private_internal_tool"
        create = Mock(
            side_effect=[
                tool_response(1, name="private_internal_tool", arguments="{}"),
                terminal_response(),
            ]
        )
        pipeline = ModelToolPipeline(
            response_create=create,
            tool_execute=Mock(side_effect=ValueError(private_error)),
            max_tool_rounds=4,
            emit_exception=Mock(),
            emit_round_limit=Mock(),
        )

        result = pipeline.run(self.make_context(), self.make_plan())

        public = result.activity.tool_results[0].to_legacy_dict()
        self.assertFalse(public["ok"])
        self.assertEqual(public["summary"], "private_internal_tool failed.")
        self.assertNotIn(private_error, json.dumps(public))
        self.assertIn(private_error, result.activity.model_tool_context[0].output)

    def test_initial_and_followup_provider_failures_use_bounded_fallbacks(self) -> None:
        initial_events = Mock()
        initial = ModelToolPipeline(
            response_create=Mock(side_effect=RuntimeError("provider secret")),
            tool_execute=Mock(),
            max_tool_rounds=4,
            emit_exception=initial_events,
            emit_round_limit=Mock(),
        ).run(self.make_context(), self.make_plan())
        self.assertEqual(initial.raw_text, INITIAL_FAILURE_TEXT)
        initial_events.assert_called_once()

        followup_events = Mock()
        followup = ModelToolPipeline(
            response_create=Mock(
                side_effect=[tool_response(1), RuntimeError("provider secret")]
            ),
            tool_execute=Mock(return_value={"ok": True}),
            max_tool_rounds=4,
            emit_exception=followup_events,
            emit_round_limit=Mock(),
        ).run(self.make_context(), self.make_plan())
        self.assertEqual(followup.raw_text, FOLLOWUP_FAILURE_TEXT)
        followup_events.assert_called_once()

    def test_exhausted_rounds_return_exact_fallback_and_emit_bound(self) -> None:
        create = Mock(side_effect=[tool_response(index) for index in range(3)])
        round_limit = Mock()
        pipeline = ModelToolPipeline(
            response_create=create,
            tool_execute=Mock(return_value={"ok": True}),
            max_tool_rounds=2,
            emit_exception=Mock(),
            emit_round_limit=round_limit,
        )

        result = pipeline.run(self.make_context(), self.make_plan())

        self.assertEqual(result.raw_text, ROUND_LIMIT_TEXT)
        self.assertEqual(len(result.activity.tool_results), 2)
        round_limit.assert_called_once_with(2)

    def test_turn_plan_preserves_confirmation_and_search_priority(self) -> None:
        base_tools = [
            {"type": "function", "name": "memory_remember"},
            {"type": "function", "name": "memory_confirm"},
            {"type": "function", "name": "send_to_body"},
        ]
        confirmation = build_tool_turn_plan(
            policy=BridgePolicy(),
            base_tools=base_tools,
            user_transcript="Yes, save it.",
            memory_available=True,
            source_available=False,
            web_search_configured=True,
            pending_memory_confirmation=True,
        )
        combined = build_tool_turn_plan(
            policy=BridgePolicy(),
            base_tools=base_tools,
            user_transcript="Search the web and remember that result.",
            memory_available=True,
            source_available=False,
            web_search_configured=True,
            pending_memory_confirmation=False,
        )

        self.assertEqual(
            confirmation.tool_choice,
            {"type": "function", "name": "memory_confirm"},
        )
        self.assertEqual(
            {tool.get("name") for tool in confirmation.tools},
            {"memory_confirm"},
        )
        self.assertIsNone(combined.tool_choice)
        self.assertTrue(combined.web_search_enabled)
        self.assertEqual(
            {tool.get("name", tool.get("type")) for tool in combined.tools},
            {"memory_remember", "web_search"},
        )


if __name__ == "__main__":
    unittest.main()
