from __future__ import annotations

import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from bridge_contracts import (
    AssistantResult,
    InteractionInput,
    ModelActivity,
    ParsedModelReply,
    PersistenceResult,
    PersistenceStatus,
    QueryInput,
)
from bridge_service import (
    BridgeService,
    ConversationTurnView,
    ConversationView,
    ServiceNotFoundError,
)


class FakeActionQueue:
    def __init__(self) -> None:
        self.actions: dict[str, dict[str, object]] = {}

    def create(self, **kwargs: object) -> tuple[dict[str, object], bool]:
        action = {"id": "action-1", "status": "queued", **kwargs}
        self.actions["action-1"] = action
        return action, True

    def list(self, **_kwargs: object) -> list[dict[str, object]]:
        return list(self.actions.values())

    def get(self, action_id: str) -> dict[str, object] | None:
        return self.actions.get(action_id)

    def claim_next(self, _target: str) -> dict[str, object] | None:
        return self.actions.get("action-1")

    def acknowledge(
        self,
        _target: str,
        action_id: str,
        status: str,
        error: str,
        result: dict[str, object] | None,
    ) -> dict[str, object]:
        if action_id not in self.actions:
            raise LookupError("missing")
        self.actions[action_id].update(status=status, error=error, result=result)
        return self.actions[action_id]


class FakeConversationStore:
    def __init__(self) -> None:
        self.session = {"id": "session-1", "title": "Shared"}
        self.raw_turns = [
            {
                "id": 1,
                "device_id": "web-console",
                "role": "user",
                "content": "hello",
                "created_at": "2026-08-12T12:00:00Z",
            }
        ]

    def active_session(self) -> dict[str, object]:
        return self.session

    def list_sessions(self, *, limit: int) -> list[dict[str, object]]:
        del limit
        return [self.session]

    def turns(self, _session_id: str) -> list[dict[str, object]]:
        return self.raw_turns

    def list_device_ids(self, _session_id: str) -> list[str]:
        return ["web-console"]


def assistant_gateway(
    transcript: str,
    *,
    device_id: str,
    response_device_id: str | None,
) -> AssistantResult:
    del transcript, device_id, response_device_id
    return AssistantResult(
        parsed=ParsedModelReply(command="BS", reply="**Typed** reply"),
        activity=ModelActivity(),
        persistence=PersistenceResult.create(
            PersistenceStatus.SKIPPED,
            backend="local",
        ),
    )


class BridgeServiceTest(unittest.TestCase):
    def make_service(
        self,
        *,
        store: object | None = None,
        history: list[dict[str, object]] | None = None,
        clock: Mock | None = None,
    ) -> BridgeService:
        local_history = history if history is not None else []
        return BridgeService(
            assistant_gateway=assistant_gateway,
            action_queue=FakeActionQueue(),
            conversation_store=store,
            conversation_backend="supabase" if store else "local",
            history_provider=lambda: local_history,
            history_clearer=local_history.clear,
            history_lock=threading.Lock(),
            plain_text=lambda value: value.replace("**", ""),
            known_device_bodies=(
                {
                    "id": "web-console",
                    "label": "Web",
                    "kind": "web",
                    "status": "active",
                    "description": "Browser body",
                },
            ),
            infrastructure_device_ids={"local-bridge"},
            monotonic_clock=clock or Mock(return_value=0.0),
            utc_now=lambda: datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc),
            presence_ttl_seconds=20,
        )

    def test_query_and_interaction_orchestrate_without_http_server(self) -> None:
        service = self.make_service()

        query = service.answer_query(
            QueryInput(
                transcript="hello",
                device_id="web-console",
                response_device_id="wearabllm-esp32",
            )
        )
        interaction = service.create_interaction(
            InteractionInput(
                transcript="hello room",
                origin_device_id="web-console",
                target_device_id="wearabllm-esp32",
            )
        )

        self.assertEqual(query.reply, "Typed reply")
        self.assertEqual(interaction.action["status"], "queued")
        self.assertEqual(interaction.action["target_device_id"], "wearabllm-esp32")

    def test_presence_uses_injected_clock(self) -> None:
        clock = Mock(side_effect=[100.0, 110.0, 121.0])
        service = self.make_service(clock=clock)

        service.touch_device("web-console")

        self.assertTrue(service.presence_for("web-console")[0])
        self.assertFalse(service.presence_for("web-console")[0])

    def test_audio_query_uses_injected_adapters_without_http_server(self) -> None:
        service = self.make_service()
        recorder = Mock()
        service.debug_wav_saver = Mock(return_value=Path("capture.wav"))
        service.wav_inspector = Mock(return_value={"valid": True, "sample_rate": 16000})
        service.transcriber = Mock(return_value="spoken hello")
        service.capture_recorder = recorder

        result = service.answer_audio_query(b"RIFF-audio", device_id="wearabllm-esp32")

        self.assertEqual(result.query.transcript, "spoken hello")
        self.assertEqual(result.query.audio_bytes, 10)
        self.assertEqual(result.query.saved_wav, "capture.wav")
        recorder.assert_called_once()

    def test_action_lookup_failure_is_explicit_after_policy_authorization(self) -> None:
        service = self.make_service()

        with self.assertRaises(ServiceNotFoundError):
            service.get_action("missing")

    def test_local_and_supabase_paths_share_internal_view_types_and_turn_shape(self) -> None:
        local = self.make_service(
            history=[
                {
                    "device_id": "web-console",
                    "role": "user",
                    "content": "hello",
                }
            ]
        ).conversation_view()
        persisted = self.make_service(store=FakeConversationStore()).conversation_view()

        self.assertIsInstance(local, ConversationView)
        self.assertIsInstance(persisted, ConversationView)
        self.assertIsInstance(local.turns[0], ConversationTurnView)
        self.assertIsInstance(persisted.turns[0], ConversationTurnView)
        self.assertEqual(
            set(local.turns[0].payload),
            set(persisted.turns[0].payload),
        )
        self.assertEqual(
            set(local.to_legacy_dict()),
            set(persisted.to_legacy_dict()),
        )

    def test_conversation_view_strips_private_model_context(self) -> None:
        store = FakeConversationStore()
        store.raw_turns[0]["metadata"] = {
            "tool_results": [{"name": "source_read"}],
            "model_tool_context": [{"output": "private"}],
        }

        view = self.make_service(store=store).conversation_view()

        metadata = view.turns[0].payload["metadata"]
        self.assertEqual(metadata, {"tool_results": [{"name": "source_read"}]})


if __name__ == "__main__":
    unittest.main()
