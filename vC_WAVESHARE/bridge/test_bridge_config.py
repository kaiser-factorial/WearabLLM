from __future__ import annotations

import unittest

from bridge_config import (
    ConfigurationError,
    parse_args,
    sanitized_startup_summary,
    validate_startup,
)


class BridgeConfigTest(unittest.TestCase):
    def test_defaults_and_environment_overrides_are_resolved_in_one_module(self) -> None:
        defaults = parse_args([], environment={})
        overridden = parse_args(
            [],
            environment={
                "WEARABLLM_PROVIDER": "openrouter",
                "WEARABLLM_STT": "local-whisper",
                "WEARABLLM_LLM_MODEL": "router/model",
                "WEARABLLM_MAX_AUDIO_BYTES": "12345",
                "WEARABLLM_HOSTED": "1",
            },
        )

        self.assertEqual(defaults.provider, "openai")
        self.assertEqual(defaults.port, 8765)
        self.assertEqual(defaults.action_backend, "local")
        self.assertEqual(overridden.provider, "openrouter")
        self.assertEqual(overridden.stt, "local-whisper")
        self.assertEqual(overridden.llm_model, "router/model")
        self.assertEqual(overridden.max_audio_bytes, 12345)
        self.assertEqual(overridden.action_backend, "supabase")

    def test_valid_dry_run_requires_no_provider_secret(self) -> None:
        args = parse_args(["--dry-run"], environment={})
        validate_startup(args, environment={})

    def test_startup_rejects_missing_and_incompatible_options(self) -> None:
        cases = (
            ([], {}, "OPENAI_API_KEY"),
            (
                ["--dry-run", "--web-search", "--provider", "openrouter"],
                {},
                "--web-search requires",
            ),
            (
                ["--dry-run", "--port", "0"],
                {},
                "--port must",
            ),
            (
                ["--dry-run", "--debug-content-logs"],
                {"WEARABLLM_HOSTED": "1", "WEARABLLM_DEVICE_TOKEN": "token"},
                "local-only",
            ),
            (
                ["--dry-run"],
                {"WEARABLLM_HOSTED": "1"},
                "WEARABLLM_DEVICE_TOKEN",
            ),
            (
                ["--dry-run", "--conversation-backend", "supabase"],
                {},
                "SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY",
            ),
        )
        for argv, environment, message in cases:
            with self.subTest(argv=argv, environment=environment):
                args = parse_args(argv, environment=environment)
                with self.assertRaisesRegex(ConfigurationError, message):
                    validate_startup(args, environment=environment)

    def test_startup_summary_is_sanitized(self) -> None:
        secret = "secret-device-token"
        args = parse_args(
            ["--dry-run", "--device-token", secret],
            environment={"WEARABLLM_HOSTED": "1"},
        )
        summary = sanitized_startup_summary(
            args,
            environment={"WEARABLLM_HOSTED": "1"},
        )

        self.assertEqual(summary["status"], "dry-run")
        self.assertNotIn(secret, repr(summary))
        self.assertNotIn("device_token", summary)


if __name__ == "__main__":
    unittest.main()
