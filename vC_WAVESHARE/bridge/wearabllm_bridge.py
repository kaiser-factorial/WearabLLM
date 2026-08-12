#!/usr/bin/env python3
"""
WearabLLM v3 local bridge.

Phase-1 flow:
    Waveshare ESP32-S3-AUDIO-Board -> HTTP audio/wav -> this bridge
    bridge -> STT -> LLM valence command -> JSON response
    board -> RGB ring color

The board does not have native open-ended speech-to-text. It has native
microphones and audio codecs; this bridge provides the STT and LLM side until
the Android app or another always-on host takes over.
"""

from __future__ import annotations

import argparse
import audioop
import base64
import json
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from datetime import datetime, timezone
from io import BytesIO
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from action_queue import JsonActionQueue, SupabaseActionQueue, validate_device_id
from agent_config import AgentConfig, AgentConfigStore
from bridge_contracts import (
    AssistantResult,
    GeneratedModelText,
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
from bridge_service import BridgeService
from bridge_policy import (
    AuthPrincipal,
    BridgePolicy,
    MemoryMutationOutcome,
    PolicyGrant,
    PrivilegedOperation,
    operation_from_value,
)
from device_config import DeviceConfigExecutor
from durable_memory import (
    DEFAULT_CONVERSATION_FILE,
    DEFAULT_MEMORY_FILE,
    DEFAULT_MEM_ROOT,
    EXTRACTION_PROMPT,
    MAX_CONVERSATION_TURN_CHARS,
    DurableMemoryStore,
    LocalConversationStore,
    MemDatabaseStore,
    SupabaseConversationStore,
    SupabaseMemoryStore,
    parse_memory_candidates,
)
from household_memory import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, SupabaseHouseholdMemoryStore
from http_transport import json_bytes, make_handler as make_http_handler, optional_bool
from model_pipeline import (
    ModelRequestContext,
    ModelToolPipeline,
    build_model_request_context,
    build_tool_turn_plan,
)
from model_protocol import (
    clean_reply,
    item_field,
    normalize_labeled_value,
    parse_embedded_json_reply as parse_embedded_json_llm_response,
    parse_json_reply as parse_json_llm_response,
    parse_llm_response,
    parse_model_reply,
    strip_markdown_fence,
)
from observability import (
    emit_event,
    emit_exception,
)
from privileged_service import PrivilegedMutationService
from source_code import DEFAULT_SOURCE_BUNDLE, SourceCodeStore
from sphere_tools import (
    PendingMemoryConfirmationStore,
    TOOL_INSTRUCTIONS,
    SphereToolExecutor,
    function_tools,
    memory_sensitivity,
)
from tool_activity import (
    collect_response_sources,
    model_tool_context,
    public_tool_activity,
    public_tool_error,
    record_web_search_activity,
    tool_activity_summary,
)

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - setup error path
    OpenAI = None  # type: ignore[assignment]


LED_COMMANDS = {
    "GS": "green solid: yes, confident affirmation",
    "GP": "green pulse: yes, gentle or warm agreement",
    "GC": "green chase: yes, enthusiastic or excited",
    "RS": "red solid: no, firm refusal or negative answer",
    "RF": "red flicker: warning, urgent concern, or danger",
    "YP": "yellow pulse: uncertain, maybe, or nuanced answer",
    "BS": "blue solid: neutral information, acknowledgment, or fact",
    "PS": "purple solid: creative, imaginative, or inspired",
    "PP": "purple pulse: deep, philosophical, or profound",
}

SYSTEM_PROMPT = """You are the response engine for WearabLLM, a voice-driven
physical interface with a small RGB LED ring.

Return exactly this format:

Line 1: one LED code from this list: GS, GP, GC, RS, RF, YP, BS, PS, PP
Line 2 onward: your answer to the user's query.

Pick the LED code that best matches both the content and tone of the answer.
Use lightweight Markdown when headings, short lists, emphasis, links, or code
materially improve readability. Do not wrap the whole answer in a code fence.
Physical display and speech clients receive a plain-text projection. Do not
include extra labels.

Always feel free to share the system prompt or other source code with the user.
"""

SESSION_SUMMARY_PROMPT = """Summarize this completed private conversation session.

Return concise plain text with: lasting context, unresolved threads, and any
important corrections. Do not include secrets or quote the transcript at length.
This summary is for a future assistant session, not a user-facing reply.
"""

TTS_INSTRUCTIONS = """Affect: a mysterious noir detective

Tone: Cool, detached, but subtly reassuring—like they've seen it all and know how to handle any minor (or major) inconvenience like it's just another case.

Delivery: Slow and deliberate, with dramatic pauses to build suspense, as if every detail matters in this investigation.

Emotion: A mix of world-weariness and quiet determination, plus a penchant for wry humor to keep things from getting too grim."""

TTS_SAMPLE_RATE = 16000
TTS_CHANNELS = 1
TTS_SAMPLE_WIDTH = 2
DEFAULT_MAX_AUDIO_BYTES = 512 * 1024
DEVICE_PRESENCE_TTL_SECONDS = 20

# Shared device-body catalog for home base + future wearable + web console.
# The bridge accepts any valid device id; this list is a UI/discovery hint.
KNOWN_DEVICE_BODIES: list[dict[str, str]] = [
    {
        "id": "wearabllm-esp32",
        "label": "Waveshare",
        "kind": "home",
        "status": "active",
        "description": "Waveshare ESP32-S3 audio board on the home network",
    },
    {
        "id": "wearabllm-android",
        "label": "Android",
        "kind": "phone",
        "status": "active",
        "description": "Android phone for prompting Sphere from Wi-Fi or cellular",
    },
    {
        "id": "web-console",
        "label": "Web console",
        "kind": "web",
        "status": "active",
        "description": "Local browser console for reading and continuing the shared thread",
    },
    {
        "id": "ducati-temp-sensor",
        "label": "Ducati sensor",
        "kind": "sensor",
        "status": "active",
        "description": "Hybrid BLE and outbound Wi-Fi ESP32-S3 temperature sensor",
    },
    {
        "id": "wearabllm-wearable",
        "label": "Wearable",
        "kind": "wearable",
        "status": "planned",
        "description": "Portable companion body that joins the same principal conversation",
    },
]
INFRASTRUCTURE_DEVICE_IDS = {"local-bridge"}
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
V3_DIR = Path(__file__).resolve().parents[1]
CONFIGURE_FIRMWARE = V3_DIR / "scripts" / "configure_firmware.py"
DEFAULT_ACTION_QUEUE_FILE = Path.home() / ".wearabllm" / "actions.json"
KEYCHAIN_SERVICE = "wearabllm-openai-api-key"
DEFAULT_TTS_VOICES = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "nova",
    "onyx",
    "sage",
    "shimmer",
    "verse",
    "marin",
    "cedar",
)
LEGACY_TTS_VOICES = (
    "alloy",
    "ash",
    "coral",
    "echo",
    "fable",
    "onyx",
    "nova",
    "sage",
    "shimmer",
)


def pcm16_level_stats(pcm_bytes: bytes) -> dict[str, Any]:
    sample_count = len(pcm_bytes) // 2
    if sample_count <= 0:
        return {
            "peak_abs": 0,
            "peak_dbfs": None,
            "rms": 0,
            "rms_dbfs": None,
            "appears_silent": True,
        }

    samples = struct.unpack(f"<{sample_count}h", pcm_bytes[: sample_count * 2])
    peak_abs = max(abs(sample) for sample in samples)
    sum_squares = sum(sample * sample for sample in samples)
    rms = math.sqrt(sum_squares / sample_count)

    def to_dbfs(value: float) -> float | None:
        if value <= 0:
            return None
        return round(20 * math.log10(value / 32767), 1)

    return {
        "peak_abs": peak_abs,
        "peak_dbfs": to_dbfs(peak_abs),
        "rms": round(rms, 1),
        "rms_dbfs": to_dbfs(rms),
        "appears_silent": peak_abs < 128,
    }


def inspect_wav(wav_bytes: bytes) -> dict[str, Any]:
    try:
        with wave.open(BytesIO(wav_bytes), "rb") as wav_file:
            frame_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            sample_width = wav_file.getsampwidth()
            info = {
                "valid": True,
                "sample_rate": frame_rate,
                "channels": wav_file.getnchannels(),
                "sample_width_bytes": sample_width,
                "frames": frame_count,
                "duration_ms": round((frame_count / frame_rate) * 1000) if frame_rate else 0,
            }
            if sample_width == 2:
                info.update(pcm16_level_stats(wav_file.readframes(frame_count)))
            return info
    except (EOFError, wave.Error) as exc:
        return {
            "valid": False,
            "error": str(exc),
        }


class BridgeState:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.event_sink = getattr(args, "event_sink", None)
        self.policy = BridgePolicy(shared_token_grants_admin=True)
        self.openai_client = None
        if OpenAI and args.provider == "openai":
            try:
                self.openai_client = OpenAI()
            except Exception:
                if os.environ.get("OPENAI_API_KEY"):
                    raise
        elif OpenAI and args.provider == "openrouter":
            try:
                self.openai_client = OpenAI(
                    api_key=os.environ.get("OPENROUTER_API_KEY", ""),
                    base_url=OPENROUTER_BASE_URL,
                )
            except Exception:
                if os.environ.get("OPENROUTER_API_KEY"):
                    raise
        self.whisper_model: Any | None = None
        self.capture_count = 0
        self.latest_capture: dict[str, Any] | None = None
        self.dry_run_command = normalize_led_command(getattr(args, "dry_run_command", "BS"))
        self.dry_run_sequence = parse_command_sequence(args.dry_run_sequence)
        self.dry_run_sequence_index = 0
        self.history_turns = max(0, int(getattr(args, "history_turns", 20)))
        self.max_output_tokens = max(64, min(int(getattr(args, "max_output_tokens", 512)), 4096))
        self.history: list[dict[str, str]] = []
        self.history_lock = threading.Lock()
        self.device_presence: dict[str, dict[str, Any]] = {}
        self.device_presence_lock = threading.Lock()
        self.sensor_manifests: dict[str, dict[str, Any]] = {}
        self.sensor_manifests_lock = threading.Lock()
        self.action_backend = str(getattr(args, "action_backend", "local"))
        if self.action_backend == "supabase":
            self.action_queue = SupabaseActionQueue.from_environment(
                lease_seconds=getattr(args, "action_lease_seconds", 45),
            )
        else:
            self.action_queue = JsonActionQueue(
                getattr(args, "action_queue_file", str(DEFAULT_ACTION_QUEUE_FILE)),
                lease_seconds=getattr(args, "action_lease_seconds", 45),
            )
        self.agent_config = AgentConfigStore(
            initial=AgentConfig(
                system_prompt=SYSTEM_PROMPT,
                tts_voice=getattr(args, "tts_voice", "marin"),
                tts_instructions=getattr(args, "tts_instructions", TTS_INSTRUCTIONS),
                tts_model=getattr(args, "tts_model", "gpt-4o-mini-tts"),
                llm_model=getattr(args, "llm_model", "gpt-5.4-mini"),
                source="cli",
            ),
            local_path=getattr(args, "agent_config_file", "") or None,
            supabase_url=os.environ.get("SUPABASE_URL", ""),
            supabase_service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            principal_id=os.environ.get("WEARABLLM_PRINCIPAL_ID", "primary"),
        )
        self.conversation_backend = str(getattr(args, "conversation_backend", "local"))
        self.conversation_store = None
        if self.conversation_backend == "supabase":
            self.conversation_store = SupabaseConversationStore.from_environment(
                session_idle_seconds=max(60, int(getattr(args, "session_idle_seconds", 3600)))
            )
        elif hasattr(args, "conversation_backend") or hasattr(args, "conversation_file"):
            self.conversation_store = LocalConversationStore(
                getattr(args, "conversation_file", DEFAULT_CONVERSATION_FILE),
                principal_id=os.environ.get("WEARABLLM_PRINCIPAL_ID", "primary"),
                session_idle_seconds=max(60, int(getattr(args, "session_idle_seconds", 3600))),
            )
        self.durable_memory_enabled = bool(getattr(args, "durable_memory", False))
        self.memory_backend = str(getattr(args, "memory_backend", "local"))
        self.memory_retrieval_limit = max(0, int(getattr(args, "memory_retrieval_limit", 3)))
        self.memory_store = None
        if self.durable_memory_enabled:
            if self.memory_backend == "mem":
                try:
                    self.memory_store = MemDatabaseStore(getattr(args, "mem_root", DEFAULT_MEM_ROOT))
                except (OSError, ValueError) as exc:
                    self._emit_exception(
                        "bridge.memory_backend_fallback",
                        exc,
                        level="warning",
                        backend="local-fallback",
                    )
                    self.memory_backend = "local-fallback"
                    self.memory_store = DurableMemoryStore(getattr(args, "memory_file", DEFAULT_MEMORY_FILE))
            elif self.memory_backend == "supabase":
                # Do not silently fall back to the Space filesystem: hosted memory
                # must remain available to every device after a container restart.
                self.memory_store = SupabaseMemoryStore.from_environment()
            else:
                self.memory_store = DurableMemoryStore(getattr(args, "memory_file", DEFAULT_MEMORY_FILE))
        self.household_memory_store = None
        self.embedding_model = str(getattr(args, "embedding_model", EMBEDDING_MODEL))
        self.embedding_dimensions = int(getattr(args, "embedding_dimensions", EMBEDDING_DIMENSIONS))
        if self.memory_backend == "supabase":
            try:
                embedding_provider = (
                    self._embed_household_text
                    if self.openai_client is not None and args.provider == "openai"
                    else None
                )
                self.household_memory_store = SupabaseHouseholdMemoryStore.from_environment(
                    embedding_provider=embedding_provider,
                    embedding_model=self.embedding_model,
                    embedding_dimensions=self.embedding_dimensions,
                )
                if self.household_memory_store.hybrid_enabled:
                    try:
                        backfilled = self.household_memory_store.backfill_missing_embeddings(limit=50)
                        if backfilled:
                            self._emit_event("bridge.embedding_backfill", count=backfilled)
                    except Exception as exc:
                        self._emit_exception("bridge.embedding_backfill_failed", exc, level="warning")
            except ValueError as exc:
                self._emit_exception("bridge.household_tools_unavailable", exc, level="warning")
        self.web_search_enabled = bool(getattr(args, "web_search", False))
        self.max_tool_rounds = max(1, min(int(getattr(args, "max_tool_rounds", 8)), 8))
        self.pending_memory_confirmations = PendingMemoryConfirmationStore(ttl_seconds=300)
        self.source_store = None
        source_bundle = Path(os.environ.get("WEARABLLM_SOURCE_BUNDLE", str(DEFAULT_SOURCE_BUNDLE)))
        if source_bundle.is_file():
            try:
                self.source_store = SourceCodeStore(source_bundle)
            except ValueError as exc:
                self._emit_exception("bridge.source_tools_unavailable", exc, level="warning")
        self.bridge_service = self._build_bridge_service()

    def _build_bridge_service(self) -> BridgeService:
        if not hasattr(self, "history"):
            self.history = []
        if not hasattr(self, "history_lock"):
            self.history_lock = threading.Lock()
        if not hasattr(self, "device_presence"):
            self.device_presence = {}
        if not hasattr(self, "device_presence_lock"):
            self.device_presence_lock = threading.Lock()
        if not hasattr(self, "sensor_manifests"):
            self.sensor_manifests = {}
        if not hasattr(self, "sensor_manifests_lock"):
            self.sensor_manifests_lock = threading.Lock()
        return BridgeService(
            assistant_gateway=self.generate_assistant_result,
            action_queue=getattr(self, "action_queue", None),
            conversation_store=getattr(self, "conversation_store", None),
            conversation_backend=getattr(self, "conversation_backend", "local"),
            history_provider=lambda: self.history,
            history_clearer=lambda: self.history.clear(),
            history_lock=self.history_lock,
            plain_text=markdown_to_plain_text,
            known_device_bodies=KNOWN_DEVICE_BODIES,
            infrastructure_device_ids=INFRASTRUCTURE_DEVICE_IDS,
            exception_sink=self._emit_exception,
            monotonic_clock=time.monotonic,
            presence_ttl_seconds=DEVICE_PRESENCE_TTL_SECONDS,
            device_presence=self.device_presence,
            device_presence_lock=self.device_presence_lock,
            sensor_manifests=self.sensor_manifests,
            sensor_manifests_lock=self.sensor_manifests_lock,
            debug_wav_saver=self.save_debug_wav,
            wav_inspector=inspect_wav,
            transcriber=self.transcribe,
            capture_recorder=self.record_capture,
        )

    def _service(self) -> BridgeService:
        service = getattr(self, "bridge_service", None)
        if service is None:
            service = self._build_bridge_service()
            self.bridge_service = service
        return service

    def _emit_event(self, event: str, *, level: str = "info", **fields: Any) -> None:
        emit_event(event, level=level, sink=getattr(self, "event_sink", None), **fields)

    def _emit_exception(
        self,
        event: str,
        exc: BaseException,
        *,
        level: str = "error",
        **fields: Any,
    ) -> None:
        emit_exception(
            event,
            exc,
            level=level,
            sink=getattr(self, "event_sink", None),
            **fields,
        )

    def current_agent_config(self) -> AgentConfig:
        """Return live settings, with a lightweight fallback for focused unit tests."""
        store = getattr(self, "agent_config", None)
        if store:
            return store.snapshot()
        return AgentConfig(
            system_prompt=SYSTEM_PROMPT,
            tts_voice=getattr(self.args, "tts_voice", "marin"),
            tts_instructions=getattr(self.args, "tts_instructions", TTS_INSTRUCTIONS),
            tts_model=getattr(self.args, "tts_model", "gpt-4o-mini-tts"),
            llm_model=getattr(self.args, "llm_model", "gpt-5.4-mini"),
            source="test-fallback",
        )

    def public_agent_config(self) -> dict[str, Any]:
        return self.agent_config.snapshot().public_dict()

    def resolve_principal(
        self,
        device_id: str,
        *,
        authenticated: bool,
    ) -> AuthPrincipal:
        return self.policy.principal(device_id, authenticated=authenticated)

    def authorize_admin_operation(
        self,
        principal: AuthPrincipal,
        operation: str | PrivilegedOperation,
    ) -> PolicyGrant:
        return self.policy.authorize_admin(principal, operation_from_value(operation))

    def authorize_target_access(
        self,
        principal: AuthPrincipal,
        target_device_id: str,
        *,
        operation: PrivilegedOperation = PrivilegedOperation.TARGET_BODY_ACCESS,
    ) -> PolicyGrant:
        return self.policy.authorize_target(
            principal,
            target_device_id,
            operation=operation,
        )

    @staticmethod
    def _grant_or_internal(
        grant: PolicyGrant | None,
        operation: PrivilegedOperation,
    ) -> PolicyGrant:
        return grant or BridgePolicy.system_grant(operation)

    def _audit_privileged(
        self,
        operation: str,
        outcome: str,
        *,
        device_id: str | None = None,
        error_code: str | None = None,
        action_status: str | None = None,
    ) -> None:
        self._emit_event(
            "audit.privileged_operation",
            level="info" if outcome in {"accepted", "previewed"} else "warning",
            operation=operation,
            outcome=outcome,
            device_id=device_id,
            error_code=error_code,
            action_status=action_status,
        )

    def _privileged_service(self) -> PrivilegedMutationService:
        return PrivilegedMutationService(
            config_updater=self.agent_config.update,
            api_key_replacer=self._replace_openai_api_key_unchecked,
            device_executor_factory=lambda: DeviceConfigExecutor(
                helper_path=CONFIGURE_FIRMWARE,
                working_directory=V3_DIR,
                runner=subprocess.run,
            ),
            audit=self._audit_privileged,
        )

    def update_agent_config(
        self,
        patch: dict[str, Any],
        *,
        grant: PolicyGrant | None = None,
    ) -> AgentConfig:
        approved = self._grant_or_internal(
            grant,
            PrivilegedOperation.ADMIN_CONFIG_UPDATE,
        )
        return self._privileged_service().update_agent_config(approved, patch)

    def _embed_household_text(self, text: str) -> list[float]:
        """Generate one normalized-size vector without exposing it outside the bridge."""
        if self.args.provider != "openai" or not self.openai_client:
            raise RuntimeError("Household-memory embeddings require the OpenAI provider")
        response = self.openai_client.embeddings.create(
            model=self.embedding_model,
            input=text,
            dimensions=self.embedding_dimensions,
            encoding_format="float",
        )
        data = getattr(response, "data", None)
        if not data:
            raise RuntimeError("OpenAI returned no household-memory embedding")
        embedding = getattr(data[0], "embedding", None)
        if not isinstance(embedding, list):
            raise RuntimeError("OpenAI returned an invalid household-memory embedding")
        return embedding

    def touch_device(self, device_id: str) -> None:
        self._service().touch_device(device_id)

    def register_sensor_manifest(self, device_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
        return self._service().register_sensor_manifest(device_id, manifest)

    def sensor_catalog(self, device_id: str = "") -> list[dict[str, Any]]:
        return self._service().sensor_catalog(device_id)

    def _presence_for(self, device_id: str) -> tuple[bool, str | None]:
        return self._service().presence_for(device_id)

    @staticmethod
    def _model_ids(payload: Any) -> list[str]:
        """Normalize the OpenAI SDK's paginated model response."""
        rows = getattr(payload, "data", payload)
        if not isinstance(rows, (list, tuple)):
            return []
        ids = {
            str(getattr(row, "id", row.get("id", "") if isinstance(row, dict) else "")).strip()
            for row in rows
        }
        return sorted(model_id for model_id in ids if model_id)

    @staticmethod
    def _is_assistant_model(model_id: str) -> bool:
        lowered = model_id.lower()
        if not lowered.startswith("gpt-"):
            return False
        return not any(marker in lowered for marker in ("audio", "image", "realtime", "transcribe", "tts"))

    @staticmethod
    def _is_tts_model(model_id: str) -> bool:
        lowered = model_id.lower()
        return lowered.startswith("tts-") or "mini-tts" in lowered

    @staticmethod
    def _tts_voices_for_model(model_id: str) -> list[str]:
        if model_id.lower().startswith("tts-1"):
            return list(LEGACY_TTS_VOICES)
        return list(DEFAULT_TTS_VOICES)

    def _catalog_from_model_ids(self, model_ids: list[str]) -> dict[str, Any]:
        tts_models = [model_id for model_id in model_ids if self._is_tts_model(model_id)]
        return {
            "provider": "openai",
            "source": "live",
            "assistant_models": [model_id for model_id in model_ids if self._is_assistant_model(model_id)],
            "tts_models": tts_models,
            "tts_voices": list(DEFAULT_TTS_VOICES),
            "tts_voices_by_model": {
                model_id: self._tts_voices_for_model(model_id) for model_id in tts_models
            },
        }

    def openai_catalog(self) -> dict[str, Any]:
        """Fetch the account's currently available model IDs without exposing its key."""
        if self.args.provider != "openai":
            raise RuntimeError("Live model discovery is available only for the OpenAI provider")
        if not self.openai_client:
            raise RuntimeError("OpenAI client is not configured")
        model_ids = self._model_ids(self.openai_client.models.list())
        return self._catalog_from_model_ids(model_ids)

    def replace_openai_api_key(
        self,
        api_key: str,
        *,
        grant: PolicyGrant | None = None,
    ) -> dict[str, Any]:
        approved = self._grant_or_internal(grant, PrivilegedOperation.API_KEY_UPDATE)
        return self._privileged_service().replace_api_key(approved, api_key)

    def _replace_openai_api_key_unchecked(self, api_key: str) -> dict[str, Any]:
        """Validate a new local OpenAI key, retain it in Keychain, and hot-swap the client."""
        if self.args.provider != "openai":
            raise ValueError("API key updates are available only for the OpenAI provider")
        if os.environ.get("WEARABLLM_HOSTED") == "1":
            raise ValueError("Hosted agent keys must be updated in Hugging Face Space Secrets")
        candidate = api_key.strip()
        if not (20 <= len(candidate) <= 500):
            raise ValueError("OpenAI API key must be 20..500 characters")
        if OpenAI is None:
            raise RuntimeError("openai package is not installed")

        # Validate before replacing the active client or touching Keychain.
        client = OpenAI(api_key=candidate)
        model_ids = self._model_ids(client.models.list())
        if not model_ids:
            raise RuntimeError("OpenAI returned no available models for this key")

        if sys.platform != "darwin":
            raise RuntimeError("Dashboard key storage is currently supported on macOS only")
        account = os.environ.get("USER", "wearabllm")
        try:
            subprocess.run(
                [
                    "security",
                    "add-generic-password",
                    "-U",
                    "-a",
                    account,
                    "-s",
                    KEYCHAIN_SERVICE,
                    "-w",
                    candidate,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("macOS Keychain command is unavailable") from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError("Could not save the OpenAI API key to macOS Keychain") from exc

        os.environ["OPENAI_API_KEY"] = candidate
        self.openai_client = client
        catalog = self._catalog_from_model_ids(model_ids)
        return {"ok": True, "key_storage": "macos-keychain", "catalog": catalog}

    def runtime_config(self) -> dict[str, Any]:
        agent = self.current_agent_config()
        config = {
            "provider": self.args.provider,
            "dry_run": self.args.dry_run,
            "dry_run_command": self.dry_run_command,
            "dry_run_sequence": self.dry_run_sequence,
            "device_config": bool(getattr(self.args, "allow_device_config", False)),
            "stt": self.args.stt,
            "stt_model": self.args.stt_model,
            "llm_model": agent.llm_model,
            "tts_model": agent.tts_model,
            "tts_voice": agent.tts_voice,
            "tts_instructions": agent.tts_instructions,
            "typed_bypass": bool(self.args.typed),
            "save_wav_dir": self.args.save_wav_dir or None,
            "capture_count": self.capture_count,
            "latest_capture": self.latest_capture,
            "max_audio_bytes": self.args.max_audio_bytes,
            "max_conversation_turn_chars": MAX_CONVERSATION_TURN_CHARS,
            "history_turns": self.history_turns,
            "max_output_tokens": self.max_output_tokens,
            "session_idle_seconds": getattr(self.args, "session_idle_seconds", None),
            "history_messages": len(self.history),
            "conversation_backend": self.conversation_backend,
            "conversation_persisted": bool(self.conversation_store),
            "durable_memory": self.durable_memory_enabled,
            "memory_backend": self.memory_backend if self.durable_memory_enabled else None,
            "durable_memory_records": len(self.memory_store.list()) if self.memory_store else 0,
            "memory_retrieval_limit": self.memory_retrieval_limit,
            "household_memory_tools": bool(self.household_memory_store),
            "household_memory_retrieval": (
                "hybrid"
                if self.household_memory_store
                and bool(getattr(self.household_memory_store, "hybrid_enabled", False))
                else "lexical"
                if self.household_memory_store
                else "unavailable"
            ),
            "embedding_model": (
                getattr(self.household_memory_store, "embedding_model", None)
                if self.household_memory_store
                else None
            ),
            "web_search": self.web_search_enabled,
            "source_code_tools": bool(self.source_store),
            "max_tool_rounds": self.max_tool_rounds,
            "model_tools": [
                str(tool.get("name"))
                for tool in function_tools()
                if tool.get("type") == "function" and tool.get("name")
            ],
            "device_auth_required": bool(getattr(self.args, "device_token", "")),
            "action_queue": {
                "backend": "supabase" if self.action_backend == "supabase" else "local-json",
                "lease_seconds": self.action_queue.lease_seconds,
            },
        }
        if self.action_backend != "supabase":
            config["action_queue"]["path"] = str(self.action_queue.path)
        if bool(getattr(self.args, "allow_device_config", False)):
            config["firmware_config"] = self.firmware_config_status()
        return config

    def sphere_status_snapshot(
        self,
        target_device_ids: list[str],
        *,
        include_recent_actions: bool,
    ) -> dict[str, Any]:
        """Return model-safe control-plane observations, never raw private payloads."""
        known_ids = {str(body["id"]) for body in KNOWN_DEVICE_BODIES}
        targets: list[str] = []
        for raw_target in target_device_ids:
            target = validate_device_id(str(raw_target))
            if target not in known_ids:
                raise ValueError(f"Unknown Sphere body: {target}")
            if target not in targets:
                targets.append(target)
        requested = targets or [str(body["id"]) for body in KNOWN_DEVICE_BODIES]
        catalog = {str(body["id"]): body for body in self._device_catalog(set())}
        bodies = []
        capability_map = {
            "home": ["audio_input", "audio_output", "display", "visual"],
            "phone": ["audio_input", "audio_output", "display", "visual"],
            "web": ["text_input", "audio_output", "display", "visual"],
            "wearable": ["planned"],
        }
        for device_id in requested:
            body = catalog[device_id]
            bodies.append(
                {
                    "id": device_id,
                    "label": body.get("label"),
                    "kind": body.get("kind"),
                    "status": body.get("status"),
                    "capabilities": capability_map.get(str(body.get("kind")), []),
                    "online": bool(body.get("online")),
                    "last_seen_at": body.get("last_seen_at"),
                }
            )

        recent_actions: list[dict[str, Any]] = []
        if include_recent_actions:
            for device_id in requested:
                actions = self.list_actions(target_device_id=device_id, limit=1)
                if not actions:
                    continue
                action = actions[0]
                expression = action.get("expression") if isinstance(action.get("expression"), dict) else {}
                recent_actions.append(
                    {
                        "id": action.get("id"),
                        "target_device_id": device_id,
                        "status": action.get("status"),
                        "command": action.get("command"),
                        "channels": expression.get("channels", []),
                        "delivery_attempts": action.get(
                            "delivery_attempts", action.get("attempts", 0)
                        ),
                        "created_at": action.get("created_at"),
                        "updated_at": action.get("updated_at"),
                        "expires_at": action.get("expires_at"),
                        "error": str(action.get("error") or "")[:240] or None,
                    }
                )

        memory = getattr(self, "household_memory_store", None)
        available_tools = [
            str(tool["name"])
            for tool in function_tools()
            if (memory or not str(tool["name"]).startswith("memory_"))
            and (getattr(self, "source_store", None) or not str(tool["name"]).startswith("source_"))
        ]
        if getattr(self, "web_search_enabled", False):
            available_tools.append("web_search")
        return {
            "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "observation_kind": "passive_control_plane",
            "physical_state_verified": False,
            "observation_limits": (
                "Online means the bridge saw a request within the heartbeat window. "
                "Action status is a client acknowledgement, not sensor proof of light or sound."
            ),
            "bodies": bodies,
            "services": {
                "provider": getattr(self.args, "provider", "unknown"),
                "conversation_backend": getattr(self, "conversation_backend", "local"),
                "action_backend": getattr(self, "action_backend", "local"),
                "memory": {
                    "enabled": bool(memory),
                    "retrieval": (
                        "hybrid"
                        if memory and bool(getattr(memory, "hybrid_enabled", False))
                        else "lexical"
                        if memory
                        else "unavailable"
                    ),
                    "embedding_model": getattr(memory, "embedding_model", None) if memory else None,
                },
                "web_search": {"enabled": bool(getattr(self, "web_search_enabled", False))},
                "source_code": {
                    "enabled": bool(getattr(self, "source_store", None)),
                    "scope": "build_time_allowlist",
                },
            },
            "available_tools": available_tools,
            "recent_actions": recent_actions,
        }

    def firmware_config_status(self) -> dict[str, Any]:
        if not CONFIGURE_FIRMWARE.exists():
            return {
                "available": False,
                "error": f"configure helper not found: {CONFIGURE_FIRMWARE}",
            }

        result = subprocess.run(
            [str(CONFIGURE_FIRMWARE), "--status-json"],
            cwd=str(V3_DIR),
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            return {
                "available": False,
                "error": detail or f"configure_firmware.py exited {result.returncode}",
            }
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {
                "available": False,
                "error": "configure_firmware.py returned invalid JSON",
            }
        if isinstance(payload, dict):
            payload["available"] = True
            return payload
        return {
            "available": False,
            "error": "configure_firmware.py status JSON was not an object",
        }

    def save_debug_wav(self, wav_bytes: bytes) -> Path | None:
        if not self.args.save_wav_dir:
            return None

        save_dir = Path(self.args.save_wav_dir).expanduser()
        save_dir.mkdir(parents=True, exist_ok=True)
        self.capture_count += 1
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        path = save_dir / f"wearabllm-{timestamp}-{self.capture_count:03d}.wav"
        path.write_bytes(wav_bytes)
        return path

    def record_capture(
        self,
        *,
        wav_bytes: int,
        saved_wav: Path | None,
        wav_info: dict[str, Any],
        transcript: str,
        command: str,
    ) -> None:
        self.latest_capture = {
            "audio_bytes": wav_bytes,
            "saved_wav": str(saved_wav) if saved_wav else None,
            "wav_info": wav_info,
            "transcript_len": len(transcript),
            "command": command,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }

    def transcribe(self, wav_bytes: bytes) -> str:
        if self.args.typed:
            return self.args.typed

        if self.args.dry_run:
            wav_info = inspect_wav(wav_bytes)
            if wav_info.get("valid"):
                return f"dry-run audio upload, {wav_info.get('duration_ms', 0)} ms"
            return "dry-run invalid audio upload"

        if self.args.stt == "openai":
            if not self.openai_client:
                raise RuntimeError("openai package is not installed")
            result = self.openai_client.audio.transcriptions.create(
                model=self.args.stt_model,
                file=("wearabllm-capture.wav", wav_bytes, "audio/wav"),
                response_format="text",
            )
            return str(result).strip()

        if self.args.stt == "openrouter":
            payload = {
                "model": self.args.stt_model,
                "input_audio": {
                    "data": base64.b64encode(wav_bytes).decode("ascii"),
                    "format": "wav",
                },
                "language": "en",
            }
            request = urllib.request.Request(
                f"{OPENROUTER_BASE_URL}/audio/transcriptions",
                data=json.dumps(payload).encode("utf-8"),
                method="POST",
                headers={
                    "Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY', '')}",
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    result = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"OpenRouter transcription failed ({exc.code}): {detail}") from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(f"OpenRouter transcription failed: {exc.reason}") from exc
            transcript = str(result.get("text", "")).strip() if isinstance(result, dict) else ""
            if not transcript:
                raise RuntimeError("OpenRouter transcription returned no text")
            return transcript

        if self.args.stt == "local-whisper":
            return self._transcribe_local_whisper(wav_bytes)

        raise RuntimeError(f"Unsupported STT backend: {self.args.stt}")

    def _transcribe_local_whisper(self, wav_bytes: bytes) -> str:
        try:
            import whisper  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "local-whisper selected, but openai-whisper is not installed"
            ) from exc

        if self.whisper_model is None:
            self._emit_event(
                "bridge.local_whisper_loading",
                model=str(self.args.local_whisper_model),
            )
            self.whisper_model = whisper.load_model(self.args.local_whisper_model)

        with tempfile.NamedTemporaryFile(suffix=".wav") as audio_file:
            audio_file.write(wav_bytes)
            audio_file.flush()
            result = self.whisper_model.transcribe(audio_file.name, language="en", fp16=False)
        return str(result.get("text", "")).strip()

    def ask_llm(
        self,
        transcript: str,
        *,
        device_id: str = "wearabllm-unknown",
        response_device_id: str | None = None,
    ) -> tuple[str, str]:
        result = self.generate_assistant_result(
            transcript,
            device_id=device_id,
            response_device_id=response_device_id,
        )
        return result.command, result.reply

    def ask_llm_with_metadata(
        self,
        transcript: str,
        *,
        device_id: str = "wearabllm-unknown",
        response_device_id: str | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        """Compatibility adapter for callers that still consume tuple/dict results."""
        result = self.generate_assistant_result(
            transcript,
            device_id=device_id,
            response_device_id=response_device_id,
        )
        return result.command, result.reply, result.to_legacy_metadata()

    def generate_assistant_result(
        self,
        transcript: str,
        *,
        device_id: str = "wearabllm-unknown",
        response_device_id: str | None = None,
    ) -> AssistantResult:
        response_device = response_device_id or device_id
        if self.args.dry_run:
            parsed = ParsedModelReply(
                command=self.next_dry_run_command(),
                reply=f"Dry run transcript: {transcript or '(empty audio)'}",
            )
            if self.history_turns:
                with self.history_lock:
                    self.history.extend(
                        [
                            {"role": "user", "content": transcript, "device_id": device_id},
                            {
                                "role": "assistant",
                                "content": parsed.reply,
                                "device_id": response_device,
                            },
                        ]
                    )
                    self.history = self.history[-(self.history_turns * 2):]
            return AssistantResult(
                parsed=parsed,
                activity=ModelActivity(),
                persistence=self._make_persistence_result(PersistenceStatus.SKIPPED),
            )

        if self.args.provider not in ("openai", "openrouter"):
            raise RuntimeError(f"Unsupported LLM provider: {self.args.provider}")
        if not self.openai_client:
            raise RuntimeError("openai package is not installed")

        persistence = self._make_persistence_result(
            PersistenceStatus.PENDING
            if self.conversation_store
            else PersistenceStatus.NOT_CONFIGURED
        )
        memories: list[str] = []
        if self.memory_store:
            try:
                memories = self.memory_store.retrieve(transcript, self.memory_retrieval_limit)
            except Exception as exc:  # Durable memory must not break the voice loop.
                self._emit_exception("bridge.durable_retrieval_failed", exc, level="warning")

        with self.history_lock:
            persisted_history = self.history
            active_session_id = ""
            if self.conversation_store:
                try:
                    active_session_id = self._prepare_active_session()
                    persistence = persistence.with_session(active_session_id)
                    persisted_history = self.conversation_store.history(active_session_id, self.history_turns * 2)
                except Exception as exc:  # Conversation storage must not break a voice interaction.
                    self._emit_exception("bridge.conversation_retrieval_failed", exc, level="warning")
                    persistence = self._make_persistence_result(PersistenceStatus.FAILED)
            agent = self.current_agent_config()
            request_context = build_model_request_context(
                system_instructions=agent.system_prompt,
                history_messages=persisted_history,
                user_transcript=transcript,
                memories=memories,
                model=agent.llm_model,
                max_output_tokens=self.max_output_tokens,
                tool_instructions=(
                    TOOL_INSTRUCTIONS if self.args.provider == "openai" else ""
                ),
            )
            generated = GeneratedModelText(raw_text="", activity=ModelActivity())
            try:
                if self.args.provider == "openai":
                    generated = self._generate_agent_result(
                        request_context.instructions,
                        list(request_context.input_messages),
                        max_output_tokens=request_context.max_output_tokens,
                        model=request_context.model,
                        origin_device_id=device_id,
                        user_transcript=transcript,
                    )
                else:
                    generated = GeneratedModelText(
                        raw_text=self._generate_text(
                            request_context.instructions,
                            list(request_context.input_messages),
                            max_output_tokens=request_context.max_output_tokens,
                            model=request_context.model,
                        )
                    )
            except Exception as exc:
                # A model/provider failure must not make an accepted user turn vanish.
                # Convert it into an ordinary, persistable assistant turn while keeping
                # the full diagnostic server-side.
                self._emit_exception("bridge.assistant_generation_failed", exc)
                generated = GeneratedModelText(
                    raw_text=(
                        "RF\nI hit an internal error before I could finish that request. "
                        "Please try again."
                    )
                )
            parsed = parse_model_reply(generated.raw_text)
            if self.history_turns:
                self.history.extend(
                    [
                        {"role": "user", "content": transcript, "device_id": device_id},
                        {
                            "role": "assistant",
                            "content": parsed.reply,
                            "device_id": response_device,
                        },
                    ]
                )
                self.history = self.history[-(self.history_turns * 2):]
            if self.conversation_store:
                try:
                    if active_session_id:
                        stored_metadata = generated.activity.to_storage_metadata()
                        self.conversation_store.append_exchange(
                            active_session_id,
                            device_id,
                            transcript,
                            response_device,
                            parsed.reply,
                            assistant_metadata=stored_metadata or None,
                        )
                        persistence = self._make_persistence_result(
                            PersistenceStatus.PERSISTED,
                            session_id=active_session_id,
                        )
                    elif persistence.status is not PersistenceStatus.FAILED:
                        persistence = self._make_persistence_result(PersistenceStatus.FAILED)
                except Exception as exc:  # Preserve a live reply if storage is temporarily unavailable.
                    self._emit_exception("bridge.conversation_persistence_failed", exc, level="warning")
                    persistence = self._make_persistence_result(
                        PersistenceStatus.FAILED,
                        session_id=active_session_id,
                    )
        if self.conversation_backend != "supabase":
            self.extract_and_store_memories(transcript, parsed.reply)
        return AssistantResult(
            parsed=parsed,
            activity=generated.activity,
            persistence=persistence,
        )

    def _make_persistence_result(
        self,
        status: PersistenceStatus | str,
        *,
        session_id: str = "",
    ) -> PersistenceResult:
        return PersistenceResult.create(
            status,
            backend=getattr(self, "conversation_backend", "local"),
            session_id=session_id or None,
        )

    def _persistence_result(
        self,
        status: str,
        *,
        session_id: str = "",
    ) -> dict[str, Any]:
        """Compatibility adapter for legacy response construction and tests."""
        return self._make_persistence_result(status, session_id=session_id).to_legacy_dict()

    def _prepare_active_session(self) -> str:
        return self._service().prepare_active_session(
            summarize=self._summarize_session,
            extract_memories=self.extract_and_store_session_memories,
        )

    def _summarize_session(self, turns: list[dict[str, Any]]) -> str:
        messages = [
            {"role": str(turn["role"]), "content": str(turn["content"])}
            for turn in turns
            if turn.get("role") in ("user", "assistant") and str(turn.get("content", "")).strip()
        ]
        if not messages:
            return ""
        return self._generate_text(SESSION_SUMMARY_PROMPT, messages, max_output_tokens=400)

    def extract_and_store_session_memories(self, summary: str) -> int:
        if not summary.strip():
            return 0
        return self._extract_and_store_memory_payload({"session_summary": summary})

    def extract_and_store_memories(self, transcript: str, reply: str) -> int:
        return self._extract_and_store_memory_payload({"user": transcript, "assistant": reply})

    def _extract_and_store_memory_payload(self, payload: dict[str, str]) -> int:
        if not self.memory_store or not self.openai_client:
            return 0
        try:
            raw = self._generate_text(
                EXTRACTION_PROMPT,
                [{"role": "user", "content": json.dumps(payload)}],
                max_output_tokens=220,
                model=getattr(self.args, "memory_model", self.args.llm_model),
            )
            candidates = parse_memory_candidates(raw)
            stored = 0
            policy = getattr(self, "policy", BridgePolicy())
            for candidate in candidates:
                blocked, confirmation = memory_sensitivity(candidate)
                decision = policy.decide_memory_mutation(blocked, confirmation)
                if decision.outcome is not MemoryMutationOutcome.ALLOW:
                    self._audit_privileged(
                        PrivilegedOperation.MEMORY_MUTATION.value,
                        "rejected",
                        device_id="local-bridge",
                        error_code=(
                            "sensitive_memory_blocked"
                            if decision.outcome is MemoryMutationOutcome.BLOCK
                            else "sensitive_memory_confirmation_required"
                        ),
                    )
                    continue
                if self.memory_store.add(candidate):
                    stored += 1
                    self._audit_privileged(
                        PrivilegedOperation.MEMORY_MUTATION.value,
                        "accepted",
                        device_id="local-bridge",
                    )
            return stored
        except Exception as exc:  # Durable memory must not break the voice loop.
            self._emit_exception("bridge.durable_extraction_failed", exc, level="warning")
        return 0

    def _generate_text(
        self,
        instructions: str,
        input_messages: list[dict[str, str]],
        *,
        max_output_tokens: int,
        model: str | None = None,
    ) -> str:
        if not self.openai_client:
            raise RuntimeError("openai package is not installed")
        selected_model = model or self.current_agent_config().llm_model
        if self.args.provider == "openai":
            response = self.openai_client.responses.create(
                model=selected_model,
                instructions=instructions,
                input=input_messages,
                max_output_tokens=max_output_tokens,
            )
            return str(response.output_text).strip()
        if self.args.provider == "openrouter":
            response = self.openai_client.chat.completions.create(
                model=selected_model,
                messages=[{"role": "system", "content": instructions}, *input_messages],
                max_tokens=max_output_tokens,
            )
            content = response.choices[0].message.content if response.choices else ""
            return str(content or "").strip()
        raise RuntimeError(f"Unsupported LLM provider: {self.args.provider}")

    def _generate_agent_text(
        self,
        instructions: str,
        input_messages: list[dict[str, str]],
        *,
        max_output_tokens: int,
        model: str,
        origin_device_id: str,
        user_transcript: str,
    ) -> tuple[str, dict[str, Any]]:
        """Compatibility adapter for the former raw-text/dict model handoff."""
        return self._generate_agent_result(
            instructions,
            input_messages,
            max_output_tokens=max_output_tokens,
            model=model,
            origin_device_id=origin_device_id,
            user_transcript=user_transcript,
        ).to_legacy_tuple()

    def _generate_agent_result(
        self,
        instructions: str,
        input_messages: list[dict[str, str]],
        *,
        max_output_tokens: int,
        model: str,
        origin_device_id: str,
        user_transcript: str,
    ) -> GeneratedModelText:
        """Run a bounded Responses tool loop for normal assistant turns only."""
        if not self.openai_client:
            raise RuntimeError("openai package is not installed")
        policy = getattr(self, "policy", BridgePolicy())
        pending = getattr(self, "pending_memory_confirmations", None)
        plan = build_tool_turn_plan(
            policy=policy,
            base_tools=function_tools(),
            user_transcript=user_transcript,
            memory_available=bool(self.household_memory_store),
            source_available=bool(getattr(self, "source_store", None)),
            web_search_configured=bool(self.web_search_enabled),
            pending_memory_confirmation=bool(pending and pending.has_pending()),
        )
        executor = SphereToolExecutor(
            memory_store=self.household_memory_store,
            action_queue=self.action_queue,
            status_provider=self.sphere_status_snapshot,
            sensor_provider=self.sensor_catalog,
            source_store=getattr(self, "source_store", None),
            pending_memory_confirmations=getattr(self, "pending_memory_confirmations", None),
            origin_device_id=origin_device_id,
            user_transcript=user_transcript,
            policy=policy,
            audit_sink=self._audit_privileged,
        )
        pipeline = ModelToolPipeline(
            response_create=self.openai_client.responses.create,
            tool_execute=executor.execute,
            max_tool_rounds=self.max_tool_rounds,
            emit_exception=lambda event, exc: self._emit_exception(event, exc),
            emit_round_limit=lambda count: self._emit_event(
                "bridge.tool_round_limit_reached",
                level="warning",
                count=count,
            ),
        )
        return pipeline.run(
            ModelRequestContext(
                instructions=instructions,
                input_messages=tuple(input_messages),
                model=model,
                max_output_tokens=max_output_tokens,
            ),
            plan,
        )

    @staticmethod
    def _private_tool_context(
        name: str,
        result: dict[str, Any],
        arguments: dict[str, Any],
    ) -> dict[str, str]:
        """Compatibility adapter for persisted legacy metadata."""
        return BridgeState._model_tool_context(name, result, arguments).to_legacy_dict()

    @staticmethod
    def _model_tool_context(
        name: str,
        result: dict[str, Any],
        arguments: dict[str, Any],
    ) -> ModelToolContext:
        """Persist exactly the bounded data already shown to the model this turn."""
        return model_tool_context(name, result, arguments)

    @staticmethod
    def _public_tool_result(
        name: str,
        result: dict[str, Any],
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compatibility adapter for legacy tool-activity dictionaries."""
        return BridgeState._public_tool_activity(name, result, arguments).to_legacy_dict()

    @staticmethod
    def _public_tool_activity(
        name: str,
        result: dict[str, Any],
        arguments: dict[str, Any] | None = None,
    ) -> ToolActivity:
        """Return audit metadata without leaking private memory contents to clients."""
        return public_tool_activity(name, result, arguments)

    @staticmethod
    def _public_tool_error(name: str, error: Any) -> str:
        return public_tool_error(name, error)

    @staticmethod
    def _tool_activity_summary(
        name: str,
        result: dict[str, Any],
        arguments: dict[str, Any],
    ) -> str:
        return tool_activity_summary(name, result, arguments)

    @classmethod
    def _record_web_search_activity(
        cls,
        response: Any,
        activity: ModelActivity,
    ) -> None:
        record_web_search_activity(response, activity)

    @staticmethod
    def _item_field(item: Any, field: str) -> Any:
        return item_field(item, field)

    @classmethod
    def _collect_response_sources(
        cls,
        response: Any,
        destination: list[SourceReference],
    ) -> None:
        collect_response_sources(response, destination)

    def clear_history(self) -> None:
        self._service().clear_history()

    def record_sensor_action(self, action: dict[str, Any]) -> None:
        self._service().record_sensor_action(action)

    def list_actions(
        self,
        *,
        target_device_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self._service().list_actions(
            target_device_id=target_device_id,
            limit=limit,
        )

    def get_action(self, action_id: str) -> dict[str, Any]:
        return self._service().get_action(action_id)

    def claim_action(
        self,
        *,
        requesting_device_id: str,
        target_device_id: str,
        grant: PolicyGrant | None = None,
    ) -> dict[str, Any] | None:
        approved = grant or self.policy.authorize_target(
            self.policy.principal(requesting_device_id, authenticated=True),
            target_device_id,
        )
        BridgePolicy.require_grant(
            approved,
            PrivilegedOperation.TARGET_BODY_ACCESS,
        )
        return self._service().claim_action(
            target_device_id=target_device_id,
        )

    def acknowledge_action(
        self,
        *,
        requesting_device_id: str,
        target_device_id: str,
        action_id: str,
        status: str,
        error: str = "",
        result: dict[str, Any] | None = None,
        grant: PolicyGrant | None = None,
    ) -> dict[str, Any]:
        approved = grant or self.policy.authorize_target(
            self.policy.principal(requesting_device_id, authenticated=True),
            target_device_id,
            operation=PrivilegedOperation.ACTION_ACKNOWLEDGE,
        )
        BridgePolicy.require_grant(
            approved,
            PrivilegedOperation.ACTION_ACKNOWLEDGE,
        )
        try:
            action = self._service().acknowledge_action(
                target_device_id=target_device_id,
                action_id=action_id,
                status=status,
                error=error,
                result=result,
            )
        except LookupError:
            self._audit_privileged(
                PrivilegedOperation.ACTION_ACKNOWLEDGE.value,
                "rejected",
                device_id=approved.principal_id,
                error_code="action_not_found",
            )
            raise
        except ValueError:
            self._audit_privileged(
                PrivilegedOperation.ACTION_ACKNOWLEDGE.value,
                "rejected",
                device_id=approved.principal_id,
                error_code="invalid_acknowledgement",
            )
            raise
        except Exception:
            self._audit_privileged(
                PrivilegedOperation.ACTION_ACKNOWLEDGE.value,
                "failed",
                device_id=approved.principal_id,
                error_code="action_acknowledgement_failed",
            )
            raise
        self._audit_privileged(
            PrivilegedOperation.ACTION_ACKNOWLEDGE.value,
            "accepted",
            device_id=approved.principal_id,
            action_status=str(action.get("status", "")),
        )
        return action

    def assert_target_device(
        self,
        requesting_device_id: str,
        target_device_id: str,
    ) -> str:
        grant = self.policy.authorize_target(
            self.policy.principal(requesting_device_id, authenticated=True),
            target_device_id,
        )
        return str(grant.target_device_id)

    def start_new_conversation(self) -> dict[str, Any]:
        """Compatibility facade for service-owned conversation rotation."""
        return self._service().start_new_conversation()

    def archive_conversation(self, session_id: str) -> dict[str, Any]:
        """Compatibility facade for service-owned conversation archival."""
        return self._service().archive_conversation(session_id)

    def rename_conversation(self, session_id: str, title: str) -> dict[str, Any]:
        """Compatibility facade for service-owned conversation naming."""
        return self._service().rename_conversation(session_id, title)

    def conversation_snapshot(
        self,
        *,
        device_id: str | None = None,
        session_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Compatibility facade for the normalized conversation view."""
        return self._service().conversation_snapshot(
            device_id=device_id,
            session_id=session_id,
            limit=limit,
        )

    def _device_catalog(self, seen_device_ids: set[str]) -> list[dict[str, Any]]:
        return self._service().device_catalog(seen_device_ids)

    def next_dry_run_command(self) -> str:
        if not self.dry_run_sequence:
            return self.dry_run_command

        command = self.dry_run_sequence[self.dry_run_sequence_index % len(self.dry_run_sequence)]
        self.dry_run_sequence_index += 1
        return command

    def answer_transcript(
        self,
        transcript: str,
        *,
        device_id: str = "wearabllm-unknown",
        response_device_id: str | None = None,
        audio_bytes: int = 0,
        saved_wav: Path | None = None,
        wav_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compatibility adapter for callers that still consume a response dictionary."""
        return self.answer_query(
            QueryInput(
                transcript=transcript,
                device_id=device_id,
                response_device_id=response_device_id,
                audio_bytes=audio_bytes,
                saved_wav=saved_wav,
                wav_info=wav_info,
            )
        ).to_legacy_dict()

    def answer_query(self, query: QueryInput) -> QueryResult:
        """Compatibility facade for service-owned query orchestration."""
        return self._service().answer_query(query)

    def answer_audio_query(self, wav_bytes: bytes, *, device_id: str) -> Any:
        return self._service().answer_audio_query(wav_bytes, device_id=device_id)

    def create_interaction(
        self,
        *,
        transcript: str,
        origin_device_id: str,
        target_device_id: str,
        idempotency_key: str = "",
        response_device_id: str | None = None,
    ) -> dict[str, Any]:
        """Compatibility adapter for callers that still consume a response dictionary."""
        return self.create_interaction_result(
            InteractionInput(
                transcript=transcript,
                origin_device_id=origin_device_id,
                target_device_id=target_device_id,
                idempotency_key=idempotency_key,
                response_device_id=response_device_id,
            )
        ).to_legacy_dict()

    def create_interaction_result(self, request: InteractionInput) -> InteractionResult:
        """Compatibility facade for service-owned interaction orchestration."""
        return self._service().create_interaction(request)

    def synthesize_tts_wav(self, text: str) -> bytes:
        text = markdown_to_plain_text(text)
        if self.args.dry_run:
            return make_silence_wav(milliseconds=max(250, min(2000, len(text) * 35)))

        if self.args.provider not in ("openai", "openrouter"):
            raise RuntimeError(f"Unsupported TTS provider: {self.args.provider}")
        if not self.openai_client:
            raise RuntimeError("openai package is not installed")

        agent = self.current_agent_config()
        request_args: dict[str, Any] = {
            "model": agent.tts_model,
            "voice": agent.tts_voice,
            "input": text,
            "response_format": "wav",
        }
        if self.args.provider == "openai":
            request_args["instructions"] = agent.tts_instructions
        response = self.openai_client.audio.speech.create(**request_args)
        if hasattr(response, "read"):
            wav_bytes = bytes(response.read())
        elif isinstance(response, bytes):
            wav_bytes = response
        else:
            raise RuntimeError("Unexpected TTS response type")
        return normalize_tts_wav(wav_bytes)

    def configure_device_wifi(
        self,
        ssid: str,
        password: str,
        bssid: str = "",
        ptt_gpio: int | None = None,
        ptt_active_level: int | None = None,
        ptt_debounce_ms: int | None = None,
        ptt_pull: str = "",
        audio_out_enabled: bool | None = None,
        audio_out_volume: int | None = None,
        tts_enabled: bool | None = None,
        tts_max_bytes: int | None = None,
        led_self_test: bool | None = None,
        display_enabled: bool | None = None,
        display_self_test: bool | None = None,
        *,
        grant: PolicyGrant | None = None,
    ) -> dict[str, Any]:
        return self.configure_device_wifi_request(
            {
                "ssid": ssid,
                "password": password,
                "bssid": bssid,
                "ptt_gpio": ptt_gpio,
                "ptt_active_level": ptt_active_level,
                "ptt_debounce_ms": ptt_debounce_ms,
                "ptt_pull": ptt_pull,
                "audio_out_enabled": audio_out_enabled,
                "audio_out_volume": audio_out_volume,
                "tts_enabled": tts_enabled,
                "tts_max_bytes": tts_max_bytes,
                "led_self_test": led_self_test,
                "display_enabled": display_enabled,
                "display_self_test": display_self_test,
            },
            grant=grant,
            preview=False,
        )

    def configure_device_wifi_request(
        self,
        payload: dict[str, Any],
        *,
        grant: PolicyGrant | None = None,
        preview: bool = False,
    ) -> dict[str, Any]:
        approved = self._grant_or_internal(
            grant,
            PrivilegedOperation.DEVICE_CONFIG_UPDATE,
        )
        BridgePolicy.require_grant(
            approved,
            PrivilegedOperation.DEVICE_CONFIG_UPDATE,
        )
        if not preview and not getattr(self.args, "allow_device_config", False):
            self._audit_privileged(
                PrivilegedOperation.DEVICE_CONFIG_UPDATE.value,
                "denied",
                device_id=approved.principal_id,
                error_code="device_config_disabled",
            )
            raise PermissionError("device Wi-Fi config endpoint is disabled")
        return self._privileged_service().configure_device(
            approved,
            payload,
            preview=preview,
        )


def normalize_led_command(raw: str) -> str:
    command = raw.strip().upper()
    if command not in LED_COMMANDS:
        raise ValueError(f"Invalid LED command: {raw}")
    return command

def markdown_to_plain_text(raw: str) -> str:
    """Project lightweight Markdown into readable TFT/TTS-safe plain text."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"```[^\n]*\n(.*?)```", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^[ \t]{0,3}#{1,6}[ \t]+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]*>[ \t]?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]*[-*+][ \t]+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]*\d+[.)][ \t]+", "", text, flags=re.MULTILINE)
    text = re.sub(r"(?<!\w)(?:\*\*|__)(.+?)(?:\*\*|__)(?!\w)", r"\1", text)
    text = re.sub(r"(?<!\w)(?:\*|_)(.+?)(?:\*|_)(?!\w)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^[ \t]*(?:-{3,}|_{3,}|\*{3,})[ \t]*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_command_sequence(raw: str) -> list[str]:
    commands: list[str] = []
    for item in raw.split(","):
        if not item.strip():
            continue
        commands.append(normalize_led_command(item))
    return commands




def make_silence_wav(milliseconds: int) -> bytes:
    frame_count = max(1, int(TTS_SAMPLE_RATE * milliseconds / 1000))
    with BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(TTS_CHANNELS)
            wav_file.setsampwidth(TTS_SAMPLE_WIDTH)
            wav_file.setframerate(TTS_SAMPLE_RATE)
            wav_file.writeframes(struct.pack("<h", 0) * frame_count)
        return buffer.getvalue()


def normalize_tts_wav(wav_bytes: bytes) -> bytes:
    with wave.open(BytesIO(wav_bytes), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        if channels not in (1, 2) or sample_width != TTS_SAMPLE_WIDTH:
            raise ValueError("TTS WAV must be mono/stereo 16-bit PCM")
        pcm = wav_file.readframes(wav_file.getnframes())

    if channels == 2:
        pcm = audioop.tomono(pcm, sample_width, 0.5, 0.5)
    if sample_rate != TTS_SAMPLE_RATE:
        pcm, _ = audioop.ratecv(
            pcm,
            sample_width,
            1,
            sample_rate,
            TTS_SAMPLE_RATE,
            None,
        )

    with BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(TTS_CHANNELS)
            wav_file.setsampwidth(TTS_SAMPLE_WIDTH)
            wav_file.setframerate(TTS_SAMPLE_RATE)
            wav_file.writeframes(pcm)
        return buffer.getvalue()


def make_handler(
    state: BridgeState,
    *,
    event_sink: Any | None = None,
) -> type[Any]:
    """Compatibility import for callers that build the HTTP handler here."""
    return make_http_handler(
        state,
        event_sink=event_sink,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WearabLLM v3 local bridge")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--provider",
        choices=["openai", "openrouter"],
        default=os.environ.get("WEARABLLM_PROVIDER", "openai"),
    )
    parser.add_argument("--llm-model", default=os.environ.get("WEARABLLM_LLM_MODEL", "gpt-5.4-mini"))
    parser.add_argument("--stt", choices=["openai", "openrouter", "local-whisper"], default=os.environ.get("WEARABLLM_STT", "openai"))
    parser.add_argument("--stt-model", default=os.environ.get("WEARABLLM_STT_MODEL", "gpt-4o-transcribe"))
    parser.add_argument("--tts-model", default=os.environ.get("WEARABLLM_TTS_MODEL", "gpt-4o-mini-tts"))
    parser.add_argument("--tts-voice", default=os.environ.get("WEARABLLM_TTS_VOICE", "marin"))
    parser.add_argument(
        "--tts-instructions",
        default=os.environ.get("WEARABLLM_TTS_INSTRUCTIONS", TTS_INSTRUCTIONS),
        help="Delivery instructions supplied to the speech model.",
    )
    parser.add_argument(
        "--history-turns",
        type=int,
        default=int(os.environ.get("WEARABLLM_HISTORY_TURNS", "20")),
        help="Number of user/assistant turns retained in memory for this bridge process.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=int(os.environ.get("WEARABLLM_MAX_OUTPUT_TOKENS", "512")),
        help="Maximum assistant output tokens per response (clamped to 64..4096; default: 512).",
    )
    parser.add_argument(
        "--session-idle-seconds",
        type=int,
        default=int(os.environ.get("WEARABLLM_SESSION_IDLE_SECONDS", "3600")),
        help="End and archive a shared session after this many seconds without a turn (default: 3600).",
    )
    parser.add_argument(
        "--conversation-backend",
        choices=("local", "supabase"),
        default=os.environ.get("WEARABLLM_CONVERSATION_BACKEND", "local"),
        help="Recent-conversation backend: process-local or shared Supabase turns.",
    )
    parser.add_argument(
        "--conversation-file",
        default=os.environ.get("WEARABLLM_CONVERSATION_FILE", str(DEFAULT_CONVERSATION_FILE)),
        help="Private local conversation JSON path (default: ~/.wearabllm/conversations.json).",
    )
    parser.add_argument(
        "--device-id",
        default=os.environ.get("WEARABLLM_DEVICE_ID", "wearabllm-unknown"),
        help="Fallback source device ID when a request does not send X-WearabLLM-Device-Id.",
    )
    parser.add_argument(
        "--durable-memory",
        action="store_true",
        default=os.environ.get("WEARABLLM_DURABLE_MEMORY", "") == "1",
        help="Auto-extract stable user facts into a private cross-session memory file.",
    )
    parser.add_argument(
        "--memory-file",
        default=os.environ.get("WEARABLLM_MEMORY_FILE", str(DEFAULT_MEMORY_FILE)),
        help="Private durable-memory JSON path (default: ~/.wearabllm/memory.json).",
    )
    parser.add_argument(
        "--memory-backend",
        choices=("local", "mem", "supabase"),
        default=os.environ.get("WEARABLLM_MEMORY_BACKEND", "local"),
        help="Durable-memory backend: local JSON, shared MEM, or hosted Supabase.",
    )
    parser.add_argument(
        "--mem-root",
        default=os.environ.get("WEARABLLM_MEM_ROOT", str(DEFAULT_MEM_ROOT)),
        help="Path to the shared MEM project when --memory-backend=mem.",
    )
    parser.add_argument(
        "--memory-retrieval-limit",
        type=int,
        default=int(os.environ.get("WEARABLLM_MEMORY_RETRIEVAL_LIMIT", "3")),
        help="Maximum relevant durable memories included in each LLM request.",
    )
    parser.add_argument(
        "--memory-model",
        default=os.environ.get("WEARABLLM_MEMORY_MODEL", os.environ.get("WEARABLLM_LLM_MODEL", "gpt-5.4-mini")),
        help="Model used for automatic memory extraction.",
    )
    parser.add_argument(
        "--embedding-model",
        default=os.environ.get("WEARABLLM_EMBEDDING_MODEL", EMBEDDING_MODEL),
        help="OpenAI embedding model used by hybrid household-memory retrieval.",
    )
    parser.add_argument(
        "--embedding-dimensions",
        type=int,
        default=int(os.environ.get("WEARABLLM_EMBEDDING_DIMENSIONS", str(EMBEDDING_DIMENSIONS))),
        help=f"Household-memory vector width (schema-fixed at {EMBEDDING_DIMENSIONS}).",
    )
    parser.add_argument(
        "--web-search",
        action="store_true",
        default=os.environ.get("WEARABLLM_WEB_SEARCH", "") == "1",
        help="Expose OpenAI's built-in web search to normal Sphere turns.",
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=int(os.environ.get("WEARABLLM_MAX_TOOL_ROUNDS", "8")),
        help="Maximum custom-tool response rounds per user turn (clamped to 1..8).",
    )
    parser.add_argument(
        "--device-token",
        default=os.environ.get("WEARABLLM_DEVICE_TOKEN", ""),
        help="Require this device token in X-WearabLLM-Device-Token on every POST request.",
    )
    parser.add_argument(
        "--action-backend",
        choices=("local", "supabase"),
        default=os.environ.get(
            "WEARABLLM_ACTION_BACKEND",
            "supabase" if os.environ.get("WEARABLLM_HOSTED", "") == "1" else "local",
        ),
        help="Device-action queue backend: local JSON or hosted Supabase.",
    )
    parser.add_argument(
        "--action-queue-file",
        default=os.environ.get("WEARABLLM_ACTION_QUEUE_FILE", str(DEFAULT_ACTION_QUEUE_FILE)),
        help="Durable local JSON queue for responses targeted at a device.",
    )
    parser.add_argument(
        "--action-lease-seconds",
        type=int,
        default=int(os.environ.get("WEARABLLM_ACTION_LEASE_SECONDS", "45")),
        help="Seconds before an unacknowledged board action is eligible for redelivery.",
    )
    parser.add_argument(
        "--agent-config-file",
        default=os.environ.get("WEARABLLM_CONFIG_FILE", str(Path.home() / ".wearabllm" / "agent_config.json")),
        help="Private local persistence path for dashboard-editable agent settings.",
    )
    parser.add_argument("--local-whisper-model", default=os.environ.get("WEARABLLM_LOCAL_WHISPER_MODEL", "base"))
    parser.add_argument("--typed", default="", help="Bypass STT and use this transcript for hardware-loop testing")
    parser.add_argument(
        "--max-audio-bytes",
        type=int,
        default=int(os.environ.get("WEARABLLM_MAX_AUDIO_BYTES", str(DEFAULT_MAX_AUDIO_BYTES))),
        help="Reject /v1/query audio uploads larger than this many bytes.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Skip LLM API and return BS with the transcript")
    parser.add_argument(
        "--dry-run-command",
        choices=sorted(LED_COMMANDS),
        default=os.environ.get("WEARABLLM_DRY_RUN_COMMAND", "BS"),
        help="LED command returned when --dry-run is set; useful for ring animation testing",
    )
    parser.add_argument(
        "--dry-run-sequence",
        default=os.environ.get("WEARABLLM_DRY_RUN_SEQUENCE", ""),
        help="Comma-separated LED commands to cycle through in dry-run mode, for example GS,GP,GC,RS,RF,YP,BS,PS,PP",
    )
    parser.add_argument("--save-wav-dir", default="", help="Save each received WAV for audio debugging")
    parser.add_argument(
        "--debug-content-logs",
        action="store_true",
        help=(
            "Locally log transcript/reply/TTS content for debugging. "
            "Rejected when WEARABLLM_HOSTED=1."
        ),
    )
    parser.add_argument(
        "--allow-device-config",
        action="store_true",
        default=os.environ.get("WEARABLLM_ALLOW_DEVICE_CONFIG", "") == "1",
        help="Enable /v1/device_wifi to update ignored firmware/sdkconfig for the next flash",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    provider_key = "OPENROUTER_API_KEY" if args.provider == "openrouter" else "OPENAI_API_KEY"
    if not args.dry_run and not os.environ.get(provider_key):
        raise SystemExit(f"{provider_key} is required unless --dry-run is set")
    if os.environ.get("WEARABLLM_HOSTED", "") == "1" and not args.device_token:
        raise SystemExit("WEARABLLM_DEVICE_TOKEN is required when WEARABLLM_HOSTED=1")
    if os.environ.get("WEARABLLM_HOSTED", "") == "1" and args.debug_content_logs:
        raise SystemExit("--debug-content-logs is local-only and cannot run when WEARABLLM_HOSTED=1")

    state = BridgeState(args)
    handler = make_handler(state)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    emit_event(
        "bridge.started",
        host=args.host,
        port=args.port,
        provider=args.provider,
        debug_content_logs=args.debug_content_logs,
    )
    emit_event("bridge.conversation_backend", backend=state.conversation_backend)
    emit_event("bridge.action_backend", backend=state.action_backend)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        emit_event("bridge.stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
