"""CLI/environment configuration, startup validation, and safe summaries."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Mapping, Sequence

from bridge_contracts import LED_COMMAND_CODES
from durable_memory import (
    DEFAULT_CONVERSATION_FILE,
    DEFAULT_MEMORY_FILE,
    DEFAULT_MEM_ROOT,
)
from household_memory import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL


DEFAULT_ACTION_QUEUE_FILE = Path.home() / ".wearabllm" / "actions.json"
DEFAULT_MAX_AUDIO_BYTES = 512 * 1024
TTS_INSTRUCTIONS = """Affect: a mysterious noir detective

Tone: Cool, detached, but subtly reassuring—like they've seen it all and know how to handle any minor (or major) inconvenience like it's just another case.

Delivery: Slow and deliberate, with dramatic pauses to build suspense, as if every detail matters in this investigation.

Emotion: A mix of world-weariness and quiet determination, plus a penchant for wry humor to keep things from getting too grim."""


class ConfigurationError(ValueError):
    """Raised before startup when runtime options cannot work together."""


def _environment(environment: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environment is None else environment


def build_parser(environment: Mapping[str, str] | None = None) -> argparse.ArgumentParser:
    env = _environment(environment)
    parser = argparse.ArgumentParser(description="WearabLLM v3 local bridge")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--provider",
        choices=["openai", "openrouter"],
        default=env.get("WEARABLLM_PROVIDER", "openai"),
    )
    parser.add_argument("--llm-model", default=env.get("WEARABLLM_LLM_MODEL", "gpt-5.4-mini"))
    parser.add_argument(
        "--stt",
        choices=["openai", "openrouter", "local-whisper"],
        default=env.get("WEARABLLM_STT", "openai"),
    )
    parser.add_argument("--stt-model", default=env.get("WEARABLLM_STT_MODEL", "gpt-4o-transcribe"))
    parser.add_argument("--tts-model", default=env.get("WEARABLLM_TTS_MODEL", "gpt-4o-mini-tts"))
    parser.add_argument("--tts-voice", default=env.get("WEARABLLM_TTS_VOICE", "marin"))
    parser.add_argument(
        "--tts-instructions",
        default=env.get("WEARABLLM_TTS_INSTRUCTIONS", TTS_INSTRUCTIONS),
        help="Delivery instructions supplied to the speech model.",
    )
    parser.add_argument(
        "--history-turns",
        type=int,
        default=int(env.get("WEARABLLM_HISTORY_TURNS", "20")),
        help="Number of user/assistant turns retained in memory for this bridge process.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=int(env.get("WEARABLLM_MAX_OUTPUT_TOKENS", "512")),
        help="Maximum assistant output tokens per response (clamped to 64..4096; default: 512).",
    )
    parser.add_argument(
        "--session-idle-seconds",
        type=int,
        default=int(env.get("WEARABLLM_SESSION_IDLE_SECONDS", "3600")),
        help="End and archive a shared session after this many seconds without a turn (default: 3600).",
    )
    parser.add_argument(
        "--conversation-backend",
        choices=("local", "supabase"),
        default=env.get("WEARABLLM_CONVERSATION_BACKEND", "local"),
        help="Recent-conversation backend: process-local or shared Supabase turns.",
    )
    parser.add_argument(
        "--conversation-file",
        default=env.get("WEARABLLM_CONVERSATION_FILE", str(DEFAULT_CONVERSATION_FILE)),
        help="Private local conversation JSON path (default: ~/.wearabllm/conversations.json).",
    )
    parser.add_argument(
        "--device-id",
        default=env.get("WEARABLLM_DEVICE_ID", "wearabllm-unknown"),
        help="Fallback source device ID when a request does not send X-WearabLLM-Device-Id.",
    )
    parser.add_argument(
        "--durable-memory",
        action="store_true",
        default=env.get("WEARABLLM_DURABLE_MEMORY", "") == "1",
        help="Auto-extract stable user facts into a private cross-session memory file.",
    )
    parser.add_argument(
        "--memory-file",
        default=env.get("WEARABLLM_MEMORY_FILE", str(DEFAULT_MEMORY_FILE)),
        help="Private durable-memory JSON path (default: ~/.wearabllm/memory.json).",
    )
    parser.add_argument(
        "--memory-backend",
        choices=("local", "mem", "supabase"),
        default=env.get("WEARABLLM_MEMORY_BACKEND", "local"),
        help="Durable-memory backend: local JSON, shared MEM, or hosted Supabase.",
    )
    parser.add_argument(
        "--mem-root",
        default=env.get("WEARABLLM_MEM_ROOT", str(DEFAULT_MEM_ROOT)),
        help="Path to the shared MEM project when --memory-backend=mem.",
    )
    parser.add_argument(
        "--memory-retrieval-limit",
        type=int,
        default=int(env.get("WEARABLLM_MEMORY_RETRIEVAL_LIMIT", "3")),
        help="Maximum relevant durable memories included in each LLM request.",
    )
    parser.add_argument(
        "--memory-model",
        default=env.get("WEARABLLM_MEMORY_MODEL", env.get("WEARABLLM_LLM_MODEL", "gpt-5.4-mini")),
        help="Model used for automatic memory extraction.",
    )
    parser.add_argument(
        "--embedding-model",
        default=env.get("WEARABLLM_EMBEDDING_MODEL", EMBEDDING_MODEL),
        help="OpenAI embedding model used by hybrid household-memory retrieval.",
    )
    parser.add_argument(
        "--embedding-dimensions",
        type=int,
        default=int(env.get("WEARABLLM_EMBEDDING_DIMENSIONS", str(EMBEDDING_DIMENSIONS))),
        help=f"Household-memory vector width (schema-fixed at {EMBEDDING_DIMENSIONS}).",
    )
    parser.add_argument(
        "--web-search",
        action="store_true",
        default=env.get("WEARABLLM_WEB_SEARCH", "") == "1",
        help="Expose OpenAI's built-in web search to normal Sphere turns.",
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=int(env.get("WEARABLLM_MAX_TOOL_ROUNDS", "8")),
        help="Maximum custom-tool response rounds per user turn (clamped to 1..8).",
    )
    parser.add_argument(
        "--device-token",
        default=env.get("WEARABLLM_DEVICE_TOKEN", ""),
        help="Require this device token in X-WearabLLM-Device-Token on every POST request.",
    )
    parser.add_argument(
        "--action-backend",
        choices=("local", "supabase"),
        default=env.get(
            "WEARABLLM_ACTION_BACKEND",
            "supabase" if env.get("WEARABLLM_HOSTED", "") == "1" else "local",
        ),
        help="Device-action queue backend: local JSON or hosted Supabase.",
    )
    parser.add_argument(
        "--action-queue-file",
        default=env.get("WEARABLLM_ACTION_QUEUE_FILE", str(DEFAULT_ACTION_QUEUE_FILE)),
        help="Durable local JSON queue for responses targeted at a device.",
    )
    parser.add_argument(
        "--action-lease-seconds",
        type=int,
        default=int(env.get("WEARABLLM_ACTION_LEASE_SECONDS", "45")),
        help="Seconds before an unacknowledged board action is eligible for redelivery.",
    )
    parser.add_argument(
        "--agent-config-file",
        default=env.get("WEARABLLM_CONFIG_FILE", str(Path.home() / ".wearabllm" / "agent_config.json")),
        help="Private local persistence path for dashboard-editable agent settings.",
    )
    parser.add_argument("--local-whisper-model", default=env.get("WEARABLLM_LOCAL_WHISPER_MODEL", "base"))
    parser.add_argument("--typed", default="", help="Bypass STT and use this transcript for hardware-loop testing")
    parser.add_argument(
        "--max-audio-bytes",
        type=int,
        default=int(env.get("WEARABLLM_MAX_AUDIO_BYTES", str(DEFAULT_MAX_AUDIO_BYTES))),
        help="Reject /v1/query audio uploads larger than this many bytes.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Skip LLM API and return BS with the transcript")
    parser.add_argument(
        "--dry-run-command",
        choices=sorted(LED_COMMAND_CODES),
        default=env.get("WEARABLLM_DRY_RUN_COMMAND", "BS"),
        help="LED command returned when --dry-run is set; useful for ring animation testing",
    )
    parser.add_argument(
        "--dry-run-sequence",
        default=env.get("WEARABLLM_DRY_RUN_SEQUENCE", ""),
        help="Comma-separated LED commands to cycle through in dry-run mode, for example GS,GP,GC,RS,RF,YP,BS,PS,PP",
    )
    parser.add_argument("--save-wav-dir", default="", help="Save each received WAV for audio debugging")
    parser.add_argument(
        "--debug-content-logs",
        action="store_true",
        help="Locally log transcript/reply/TTS content for debugging. Rejected when WEARABLLM_HOSTED=1.",
    )
    parser.add_argument(
        "--allow-device-config",
        action="store_true",
        default=env.get("WEARABLLM_ALLOW_DEVICE_CONFIG", "") == "1",
        help="Enable /v1/device_wifi to update ignored firmware/sdkconfig for the next flash",
    )
    return parser


def parse_args(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> argparse.Namespace:
    return build_parser(environment).parse_args(argv)


def validate_startup(
    args: argparse.Namespace,
    *,
    environment: Mapping[str, str] | None = None,
) -> None:
    env = _environment(environment)
    hosted = env.get("WEARABLLM_HOSTED", "") == "1"
    required_keys: set[str] = set()
    if not args.dry_run:
        required_keys.add("OPENROUTER_API_KEY" if args.provider == "openrouter" else "OPENAI_API_KEY")
        if args.stt == "openrouter":
            required_keys.add("OPENROUTER_API_KEY")
        elif args.stt == "openai":
            required_keys.add("OPENAI_API_KEY")
    missing = sorted(key for key in required_keys if not env.get(key))
    if missing:
        raise ConfigurationError(f"{', '.join(missing)} is required unless --dry-run is set")
    if hosted and not args.device_token:
        raise ConfigurationError("WEARABLLM_DEVICE_TOKEN is required when WEARABLLM_HOSTED=1")
    if hosted and args.debug_content_logs:
        raise ConfigurationError("--debug-content-logs is local-only and cannot run when WEARABLLM_HOSTED=1")
    if args.provider != "openai" and args.web_search:
        raise ConfigurationError("--web-search requires --provider openai")
    uses_supabase = any(
        backend == "supabase"
        for backend in (
            args.conversation_backend,
            args.action_backend,
            args.memory_backend,
        )
    )
    if uses_supabase:
        missing_supabase = [
            key
            for key in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
            if not env.get(key)
        ]
        if missing_supabase:
            raise ConfigurationError(
                f"{', '.join(missing_supabase)} is required for Supabase backends"
            )
    if not 1 <= int(args.port) <= 65535:
        raise ConfigurationError("--port must be in the range 1..65535")
    if int(args.max_audio_bytes) <= 0:
        raise ConfigurationError("--max-audio-bytes must be greater than zero")
    if int(args.action_lease_seconds) <= 0:
        raise ConfigurationError("--action-lease-seconds must be greater than zero")


def sanitized_startup_summary(
    args: argparse.Namespace,
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    del environment
    return {
        "host": args.host,
        "port": int(args.port),
        "provider": args.provider,
        "status": "dry-run" if args.dry_run else "live",
        "debug_content_logs": bool(args.debug_content_logs),
    }
