from __future__ import annotations

import json
import unittest
from pathlib import Path

from bridge_contracts import (
    ContractError,
    InteractionInput,
    InteractionResult,
    ModelActivity,
    ModelToolContext,
    ParsedModelReply,
    PersistenceResult,
    PersistenceStatus,
    QueryInput,
    QueryResult,
    SourceReference,
    ToolActivity,
)
from wearabllm_bridge import parse_llm_response, parse_model_reply


FIXTURES = Path(__file__).resolve().parent / "contract_fixtures" / "v1" / "golden_shapes.json"


class PersistenceResultTest(unittest.TestCase):
    def test_failed_result_round_trips_with_safe_legacy_error(self) -> None:
        result = PersistenceResult.create(
            PersistenceStatus.FAILED,
            backend="supabase",
            session_id="session-1",
        )

        payload = result.to_legacy_dict()

        self.assertEqual(PersistenceResult.from_legacy_dict(payload), result)
        self.assertEqual(payload["error_code"], "conversation_write_failed")
        self.assertNotIn("database", payload["message"].lower())

    def test_rejects_unknown_status_and_malformed_failed_result(self) -> None:
        with self.assertRaises(ContractError):
            PersistenceResult.create("lost", backend="supabase")
        with self.assertRaises(ContractError):
            PersistenceResult.from_legacy_dict(
                {"status": "failed", "backend": "supabase", "session_id": None}
            )


class ModelContractTest(unittest.TestCase):
    def test_model_activity_round_trip_preserves_public_and_private_metadata(self) -> None:
        activity = ModelActivity(
            sources=[SourceReference(title="Example", url="https://example.test/source")],
            tool_results=[
                ToolActivity(
                    name="web_search",
                    ok=True,
                    summary="Web searched — 1 source",
                    details={"source_count": 1},
                )
            ],
            model_tool_context=[
                ModelToolContext(name="web_search", arguments="{}", output='{"ok": true}')
            ],
        )

        payload = activity.to_legacy_dict()

        self.assertEqual(ModelActivity.from_legacy_dict(payload), activity)
        payload["sources"][0]["title"] = "mutated"
        payload["tool_results"][0]["source_count"] = 99
        self.assertEqual(activity.sources[0].title, "Example")
        self.assertEqual(activity.tool_results[0].details["source_count"], 1)

    def test_typed_parser_keeps_tuple_parser_compatible(self) -> None:
        raw = '```json\n{"command":"GP","reply":"Keep going gently."}\n```'

        typed = parse_model_reply(raw)

        self.assertEqual(typed, ParsedModelReply(command="GP", reply="Keep going gently."))
        self.assertEqual(parse_llm_response(raw), typed.to_legacy_tuple())

    def test_rejects_invalid_model_contract_values(self) -> None:
        with self.assertRaises(ContractError):
            ParsedModelReply(command="XX", reply="Nope")
        with self.assertRaises(ContractError):
            ModelActivity(sources=[{"title": "not typed", "url": "https://example.test"}])
        with self.assertRaises(ContractError):
            ToolActivity(name="tool", ok="yes", summary="wrong")


class ServiceContractTest(unittest.TestCase):
    def make_query_result(self) -> QueryResult:
        return QueryResult(
            command="BS",
            reply="A typed answer.",
            transcript="A typed question?",
            audio_bytes=0,
            saved_wav=None,
            wav_info=None,
            sources=(SourceReference(title="Docs", url="https://example.test/docs"),),
            tool_results=(),
            persistence=PersistenceResult.create(
                PersistenceStatus.SKIPPED,
                backend="local",
            ),
        )

    def test_query_round_trip_matches_golden_v1_shape(self) -> None:
        expected = json.loads(FIXTURES.read_text(encoding="utf-8"))["query.success"]
        result = self.make_query_result()

        payload = result.to_legacy_dict()

        self.assertEqual(sorted(payload), expected["top_level_keys"])
        self.assertEqual(
            sorted(payload["persistence"]),
            expected["nested_keys"]["persistence"],
        )
        self.assertEqual(QueryResult.from_legacy_dict(payload), result)

    def test_inputs_and_outputs_defensively_copy_mutable_payloads(self) -> None:
        wav_info = {"channels": 1}
        query_input = QueryInput(transcript="hello", wav_info=wav_info)
        wav_info["channels"] = 2
        self.assertEqual(query_input.wav_info, {"channels": 1})

        action = {"id": "action-1", "expression": {"channels": ["visual"]}}
        interaction = InteractionResult(
            query=self.make_query_result(),
            action=action,
            action_created=True,
        )
        action["id"] = "mutated"
        payload = interaction.to_legacy_dict()
        payload["action"]["id"] = "also-mutated"
        self.assertEqual(interaction.action["id"], "action-1")

    def test_rejects_invalid_query_and_interaction_inputs(self) -> None:
        with self.assertRaises(ContractError):
            QueryInput(transcript="hello", audio_bytes=-1)
        with self.assertRaises(ContractError):
            QueryResult.from_legacy_dict({"command": "BS"})
        with self.assertRaises(ContractError):
            InteractionInput(
                transcript="",
                origin_device_id="web-console",
                target_device_id="wearabllm-esp32",
            )


if __name__ == "__main__":
    unittest.main()
