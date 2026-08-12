from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from model_protocol import (
    FallbackState,
    ModelOutputState,
    ParsedReplyState,
    TerminalReplyState,
    ToolRequestState,
    classify_response,
    parse_model_output,
)


FIXTURES = json.loads(
    (Path(__file__).parent / "model_fixtures" / "v1" / "model_protocol.json").read_text(
        encoding="utf-8"
    )
)


class ModelProtocolFixtureTest(unittest.TestCase):
    def test_parser_fixtures_preserve_legacy_results_with_explicit_states(self) -> None:
        for fixture in FIXTURES:
            with self.subTest(fixture=fixture["name"]):
                state = parse_model_output(fixture["raw"])
                self.assertEqual(state.state.value, fixture["state"])
                self.assertEqual(state.parsed.command, fixture["command"])
                self.assertEqual(state.parsed.reply, fixture["reply"])
                if isinstance(state, ParsedReplyState):
                    self.assertEqual(state.strategy, fixture["strategy"])
                else:
                    self.assertIsInstance(state, FallbackState)
                    self.assertEqual(state.reason, fixture["reason"])

    def test_response_classification_distinguishes_tool_and_terminal_states(self) -> None:
        tool_response = SimpleNamespace(
            id="resp-tool",
            output=[
                SimpleNamespace(
                    type="function_call",
                    name="memory_search",
                    call_id="call-1",
                    arguments='{"query":"tea","limit":5}',
                )
            ],
            output_text="",
        )
        terminal_response = SimpleNamespace(
            id="resp-terminal",
            output=[],
            output_text="BS\nDone.",
        )

        tool_state = classify_response(tool_response)
        terminal_state = classify_response(terminal_response)

        self.assertIsInstance(tool_state, ToolRequestState)
        self.assertEqual(tool_state.state, ModelOutputState.TOOL_REQUEST)
        self.assertEqual(tool_state.requests[0].name, "memory_search")
        self.assertIsInstance(terminal_state, TerminalReplyState)
        self.assertEqual(terminal_state.state, ModelOutputState.TERMINAL_REPLY)
        self.assertEqual(terminal_state.raw_text, "BS\nDone.")


if __name__ == "__main__":
    unittest.main()
