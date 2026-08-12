from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from provider_adapters import (
    SDKProviderAdapter,
    create_provider_client,
    validated_openai_client,
)


class ProviderAdaptersTest(unittest.TestCase):
    def test_openai_text_and_tool_responses_use_responses_api(self) -> None:
        create = Mock(return_value=SimpleNamespace(output_text="BS\nDone."))
        adapter = SDKProviderAdapter(
            SimpleNamespace(responses=SimpleNamespace(create=create)),
            "openai",
        )

        text = adapter.generate_text(
            "system",
            ({"role": "user", "content": "hello"},),
            model="model",
            max_output_tokens=200,
        )
        adapter.create_response(model="model", input=[])

        self.assertEqual(text, "BS\nDone.")
        self.assertEqual(create.call_count, 2)
        self.assertEqual(create.call_args_list[0].kwargs["instructions"], "system")

    def test_openrouter_text_uses_chat_completions(self) -> None:
        create = Mock(
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="BS\nRouter"))]
            )
        )
        adapter = SDKProviderAdapter(
            SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
            "openrouter",
        )

        text = adapter.generate_text(
            "system",
            ({"role": "user", "content": "hello"},),
            model="router/model",
            max_output_tokens=200,
        )

        self.assertEqual(text, "BS\nRouter")
        self.assertEqual(create.call_args.kwargs["messages"][0]["role"], "system")

    def test_tts_keeps_openai_instructions_provider_specific(self) -> None:
        wav = b"RIFF-data"
        openai_create = Mock(return_value=wav)
        router_create = Mock(return_value=wav)
        openai = SDKProviderAdapter(
            SimpleNamespace(audio=SimpleNamespace(speech=SimpleNamespace(create=openai_create))),
            "openai",
        )
        router = SDKProviderAdapter(
            SimpleNamespace(audio=SimpleNamespace(speech=SimpleNamespace(create=router_create))),
            "openrouter",
        )

        self.assertEqual(
            openai.synthesize("hello", model="tts", voice="marin", instructions="calm"),
            wav,
        )
        self.assertEqual(
            router.synthesize("hello", model="tts", voice="marin", instructions="calm"),
            wav,
        )
        self.assertEqual(openai_create.call_args.kwargs["instructions"], "calm")
        self.assertNotIn("instructions", router_create.call_args.kwargs)

    def test_client_factory_preserves_provider_specific_construction(self) -> None:
        factory = Mock(return_value=object())
        create_provider_client(factory, "openai", environment={"OPENAI_API_KEY": "x"})
        create_provider_client(
            factory,
            "openrouter",
            environment={"OPENROUTER_API_KEY": "router-key"},
        )

        self.assertEqual(factory.call_args_list[0].args, ())
        self.assertEqual(factory.call_args_list[1].kwargs["api_key"], "router-key")
        self.assertEqual(
            factory.call_args_list[1].kwargs["base_url"],
            "https://openrouter.ai/api/v1",
        )

    def test_candidate_key_client_must_discover_a_model(self) -> None:
        valid_client = SimpleNamespace(
            models=SimpleNamespace(
                list=Mock(return_value=SimpleNamespace(data=[SimpleNamespace(id="gpt-test")]))
            )
        )
        client, model_ids = validated_openai_client(
            Mock(return_value=valid_client),
            "candidate-key",
        )
        self.assertIs(client, valid_client)
        self.assertEqual(model_ids, ["gpt-test"])

        invalid_client = SimpleNamespace(
            models=SimpleNamespace(list=Mock(return_value=SimpleNamespace(data=[])))
        )
        with self.assertRaisesRegex(RuntimeError, "no available models"):
            validated_openai_client(Mock(return_value=invalid_client), "candidate-key")


if __name__ == "__main__":
    unittest.main()
