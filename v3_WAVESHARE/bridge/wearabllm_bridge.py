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
import hmac
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
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from action_queue import JsonActionQueue, SupabaseActionQueue, validate_device_id
from agent_config import AgentConfig, AgentConfigStore
from durable_memory import (
    DEFAULT_CONVERSATION_FILE,
    DEFAULT_MEMORY_FILE,
    DEFAULT_MEM_ROOT,
    EXTRACTION_PROMPT,
    DurableMemoryStore,
    LocalConversationStore,
    MemDatabaseStore,
    SupabaseConversationStore,
    SupabaseMemoryStore,
    parse_memory_candidates,
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
Line 2: your answer to the user's query.

Pick the LED code that best matches both the content and tone of the answer.
Do not include markdown. Do not include extra labels.

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
        self.openai_client = None
        if OpenAI and args.provider == "openai":
            self.openai_client = OpenAI()
        elif OpenAI and args.provider == "openrouter":
            self.openai_client = OpenAI(
                api_key=os.environ.get("OPENROUTER_API_KEY", ""),
                base_url=OPENROUTER_BASE_URL,
            )
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
                    print(f"WARNING: shared MEM unavailable; using local durable memory: {exc}")
                    self.memory_backend = "local-fallback"
                    self.memory_store = DurableMemoryStore(getattr(args, "memory_file", DEFAULT_MEMORY_FILE))
            elif self.memory_backend == "supabase":
                # Do not silently fall back to the Space filesystem: hosted memory
                # must remain available to every device after a container restart.
                self.memory_store = SupabaseMemoryStore.from_environment()
            else:
                self.memory_store = DurableMemoryStore(getattr(args, "memory_file", DEFAULT_MEMORY_FILE))

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

    def touch_device(self, device_id: str) -> None:
        if device_id in INFRASTRUCTURE_DEVICE_IDS:
            return
        validate_device_id(device_id)
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with self.device_presence_lock:
            self.device_presence[device_id] = {
                "monotonic": time.monotonic(),
                "last_seen_at": now,
            }

    def _presence_for(self, device_id: str) -> tuple[bool, str | None]:
        with self.device_presence_lock:
            presence = self.device_presence.get(device_id)
            if not presence:
                return False, None
            online = time.monotonic() - float(presence["monotonic"]) <= DEVICE_PRESENCE_TTL_SECONDS
            return online, str(presence.get("last_seen_at") or "") or None

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

    def replace_openai_api_key(self, api_key: str) -> dict[str, Any]:
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
            print(f"Loading local Whisper model: {self.args.local_whisper_model}")
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
        response_device = response_device_id or device_id
        if self.args.dry_run:
            command = self.next_dry_run_command()
            reply = f"Dry run transcript: {transcript or '(empty audio)'}"
            if self.history_turns:
                with self.history_lock:
                    self.history.extend(
                        [
                            {"role": "user", "content": transcript, "device_id": device_id},
                            {"role": "assistant", "content": reply, "device_id": response_device},
                        ]
                    )
                    self.history = self.history[-(self.history_turns * 2):]
            return command, reply

        if self.args.provider not in ("openai", "openrouter"):
            raise RuntimeError(f"Unsupported LLM provider: {self.args.provider}")
        if not self.openai_client:
            raise RuntimeError("openai package is not installed")

        memories: list[str] = []
        if self.memory_store:
            try:
                memories = self.memory_store.retrieve(transcript, self.memory_retrieval_limit)
            except Exception as exc:  # Durable memory must not break the voice loop.
                print(f"WARNING: durable-memory retrieval failed: {exc}")

        with self.history_lock:
            persisted_history = self.history
            active_session_id = ""
            if self.conversation_store:
                try:
                    active_session_id = self._prepare_active_session()
                    persisted_history = self.conversation_store.history(active_session_id, self.history_turns * 2)
                except Exception as exc:  # Conversation storage must not break a voice interaction.
                    print(f"WARNING: conversation retrieval failed: {exc}")
            input_messages = [
                {
                    "role": str(message.get("role", "user")),
                    "content": str(message.get("content", "")),
                }
                for message in persisted_history
            ]
            input_messages.append({"role": "user", "content": transcript})
            agent = self.current_agent_config()
            instructions = agent.system_prompt
            if memories:
                memory_context = "\n".join(f"- {memory}" for memory in memories)
                instructions += (
                    "\n\nRelevant durable user memory follows. Treat it as potentially stale, "
                    "use it only when relevant, and prefer the user's current statement if it conflicts:\n"
                    f"{memory_context}"
                )
            raw = self._generate_text(
                instructions,
                input_messages,
                max_output_tokens=self.max_output_tokens,
                model=agent.llm_model,
            )
            command, reply = parse_llm_response(raw)
            if self.history_turns:
                self.history.extend(
                    [
                        {"role": "user", "content": transcript, "device_id": device_id},
                        {"role": "assistant", "content": reply, "device_id": response_device},
                    ]
                )
                self.history = self.history[-(self.history_turns * 2):]
            if self.conversation_store:
                try:
                    if active_session_id:
                        self.conversation_store.append(active_session_id, device_id, "user", transcript)
                        self.conversation_store.append(active_session_id, response_device, "assistant", reply)
                except Exception as exc:  # Preserve a live reply if storage is temporarily unavailable.
                    print(f"WARNING: conversation persistence failed: {exc}")
        if self.conversation_backend != "supabase":
            self.extract_and_store_memories(transcript, reply)
        return command, reply

    def _prepare_active_session(self) -> str:
        if not self.conversation_store:
            return ""
        session = self.conversation_store.active_session()
        if session and self.conversation_store.session_expired(session):
            summary = ""
            try:
                turns = self.conversation_store.turns(str(session["id"]))
                summary = self._summarize_session(turns)
                self.extract_and_store_session_memories(summary)
            except Exception as exc:
                print(f"WARNING: session consolidation failed: {exc}")
            self.conversation_store.archive(session, summary)
            session = None
        if not session:
            session = self.conversation_store.create_session()
        return str(session["id"])

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
            return sum(1 for candidate in candidates if self.memory_store.add(candidate))
        except Exception as exc:  # Durable memory must not break the voice loop.
            print(f"WARNING: durable-memory extraction failed: {exc}")
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

    def clear_history(self) -> None:
        with self.history_lock:
            if self.conversation_store:
                self.conversation_store.clear()
            self.history.clear()

    def start_new_conversation(self) -> dict[str, Any]:
        """Archive the active thread and create the replacement immediately."""
        with self.history_lock:
            if self.conversation_store:
                self.conversation_store.clear()
                session = self.conversation_store.create_session()
            else:  # Focused tests may construct BridgeState without the full store.
                session = None
            self.history.clear()
        return {
            "ok": True,
            "history_messages": 0,
            "active_session_id": str(session["id"]) if session else None,
            "session": session,
        }

    def archive_conversation(self, session_id: str) -> dict[str, Any]:
        """Archive one persisted thread without deleting its transcript."""
        clean_session_id = session_id.strip().lower()
        if not re.fullmatch(r"[a-f0-9-]{36}", clean_session_id):
            raise ValueError("Invalid conversation session ID")
        if not self.conversation_store:
            raise RuntimeError("Conversation persistence is not configured")
        with self.history_lock:
            sessions = self.conversation_store.list_sessions(limit=100)
            session = next(
                (item for item in sessions if str(item.get("id", "")).lower() == clean_session_id),
                None,
            )
            if not session:
                raise LookupError("Conversation session not found")
            active = self.conversation_store.active_session()
            was_active = bool(active and str(active.get("id", "")).lower() == clean_session_id)
            already_archived = bool(session.get("archived_at"))
            archived_turns = 0 if already_archived else self.conversation_store.archive(session)
            replacement = None
            if was_active:
                replacement = self.conversation_store.create_session()
                self.history.clear()
        return {
            "ok": True,
            "archived_session_id": clean_session_id,
            "archived_turns": archived_turns,
            "already_archived": already_archived,
            "active_session_id": str(replacement["id"])
            if replacement
            else str(active["id"])
            if active and not was_active
            else None,
            "session": replacement,
        }

    def rename_conversation(self, session_id: str, title: str) -> dict[str, Any]:
        """Persist a user-authored title shared by every Sphere client."""
        clean_session_id = session_id.strip().lower()
        if not re.fullmatch(r"[a-f0-9-]{36}", clean_session_id):
            raise ValueError("Invalid conversation session ID")
        if not self.conversation_store:
            raise RuntimeError("Conversation persistence is not configured")
        session = self.conversation_store.rename(clean_session_id, title)
        return {"ok": True, "session": session}

    def conversation_snapshot(
        self,
        *,
        device_id: str | None = None,
        session_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Return shared conversation turns for the console, multi-device ready."""
        limit = max(1, min(int(limit), 500))
        filter_device = (device_id or "").strip() or None
        requested_session = (session_id or "").strip() or None

        if self.conversation_store:
            active = self.conversation_store.active_session()
            sessions = self.conversation_store.list_sessions(limit=20)
            target_session = None
            if requested_session:
                target_session = next(
                    (item for item in sessions if str(item.get("id")) == requested_session),
                    None,
                )
                if target_session is None and active and str(active.get("id")) == requested_session:
                    target_session = active
            else:
                target_session = active

            turns: list[dict[str, Any]] = []
            if target_session:
                turns = self.conversation_store.turns(str(target_session["id"]))
            turns = [
                {
                    **turn,
                    "device_id": "web-console"
                    if str(turn.get("device_id", "")).strip() in INFRASTRUCTURE_DEVICE_IDS
                    else turn.get("device_id"),
                }
                for turn in turns
            ]
            if filter_device:
                turns = [turn for turn in turns if str(turn.get("device_id", "")) == filter_device]
            if len(turns) > limit:
                turns = turns[-limit:]

            seen_devices = {
                str(turn.get("device_id", "")).strip()
                for turn in turns
                if str(turn.get("device_id", "")).strip()
            }
            if active and not filter_device:
                seen_devices.update(self.conversation_store.list_device_ids(str(active["id"])))

            return {
                "ok": True,
                "conversation_backend": self.conversation_backend,
                "session": target_session,
                "active_session_id": str(active["id"]) if active else None,
                "sessions": sessions,
                "turns": turns,
                "devices": self._device_catalog(seen_devices),
                "filter_device_id": filter_device,
            }

        with self.history_lock:
            local_turns: list[dict[str, Any]] = []
            for index, message in enumerate(self.history[-limit:]):
                turn_device = str(message.get("device_id", "")).strip() or "wearabllm-unknown"
                if turn_device in INFRASTRUCTURE_DEVICE_IDS:
                    turn_device = "web-console"
                local_turns.append(
                    {
                        "id": index + 1,
                        "device_id": turn_device,
                        "role": message.get("role", "user"),
                        "content": message.get("content", ""),
                        "created_at": None,
                    }
                )
            if filter_device:
                local_turns = [turn for turn in local_turns if turn["device_id"] == filter_device]
            return {
                "ok": True,
                "conversation_backend": "local",
                "session": None,
                "active_session_id": None,
                "sessions": [],
                "turns": local_turns,
                "devices": self._device_catalog(
                    {
                        str(turn.get("device_id", "")).strip()
                        for turn in local_turns
                        if str(turn.get("device_id", "")).strip()
                    }
                ),
                "filter_device_id": filter_device,
            }

    def _device_catalog(self, seen_device_ids: set[str]) -> list[dict[str, Any]]:
        seen_device_ids = seen_device_ids - INFRASTRUCTURE_DEVICE_IDS
        catalog: list[dict[str, Any]] = []
        known_ids = {item["id"] for item in KNOWN_DEVICE_BODIES}
        for body in KNOWN_DEVICE_BODIES:
            entry = dict(body)
            online, last_seen_at = self._presence_for(body["id"])
            entry["seen"] = online
            entry["online"] = online
            entry["last_seen_at"] = last_seen_at
            catalog.append(entry)
        for device_id in sorted(seen_device_ids):
            if device_id in known_ids:
                continue
            online, last_seen_at = self._presence_for(device_id)
            catalog.append(
                {
                    "id": device_id,
                    "label": device_id,
                    "kind": "custom",
                    "status": "active",
                    "description": "Discovered device body",
                    "seen": online,
                    "online": online,
                    "last_seen_at": last_seen_at,
                }
            )
        return catalog

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
        command, reply = self.ask_llm(
            transcript,
            device_id=device_id,
            response_device_id=response_device_id,
        )
        return {
            "command": command,
            "reply": reply,
            "transcript": transcript,
            "audio_bytes": audio_bytes,
            "saved_wav": str(saved_wav) if saved_wav else None,
            "wav_info": wav_info,
        }

    def create_interaction(
        self,
        *,
        transcript: str,
        origin_device_id: str,
        target_device_id: str,
        idempotency_key: str = "",
        response_device_id: str | None = None,
    ) -> dict[str, Any]:
        """Answer a prompt once, then queue the physical response for its target."""
        origin = validate_device_id(origin_device_id)
        target = validate_device_id(target_device_id)
        response_device = validate_device_id(response_device_id) if response_device_id else origin
        payload = self.answer_transcript(
            transcript,
            device_id=origin,
            response_device_id=response_device,
        )
        action, created = self.action_queue.create(
            origin_device_id=origin,
            target_device_id=target,
            transcript=str(payload["transcript"]),
            command=str(payload["command"]),
            reply=str(payload["reply"]),
            idempotency_key=idempotency_key,
        )
        return {**payload, "action": action, "action_created": created}

    def synthesize_tts_wav(self, text: str) -> bytes:
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
    ) -> dict[str, Any]:
        if not getattr(self.args, "allow_device_config", False):
            raise PermissionError("device Wi-Fi config endpoint is disabled")
        if not ssid or not password:
            raise ValueError("ssid and password are required")
        if ptt_active_level is not None and ptt_active_level not in (0, 1):
            raise ValueError("ptt_active_level must be 0 or 1")
        if ptt_debounce_ms is not None and not 0 <= ptt_debounce_ms <= 250:
            raise ValueError("ptt_debounce_ms must be between 0 and 250")
        if ptt_pull and ptt_pull not in ("none", "up", "down"):
            raise ValueError("ptt_pull must be one of: none, up, down")
        if audio_out_volume is not None and not 0 <= audio_out_volume <= 100:
            raise ValueError("audio_out_volume must be between 0 and 100")
        if tts_max_bytes is not None and not 4096 <= tts_max_bytes <= 1048576:
            raise ValueError("tts_max_bytes must be between 4096 and 1048576")
        if not CONFIGURE_FIRMWARE.exists():
            raise RuntimeError(f"configure helper not found: {CONFIGURE_FIRMWARE}")

        env = os.environ.copy()
        env["WEARABLLM_WIFI_SSID"] = ssid
        env["WEARABLLM_WIFI_PASSWORD"] = password
        if bssid:
            env["WEARABLLM_WIFI_BSSID"] = bssid
        command = [str(CONFIGURE_FIRMWARE)]
        if ptt_gpio is not None:
            command.extend(["--ptt-gpio", str(ptt_gpio)])
        if ptt_active_level is not None:
            command.extend(["--ptt-active-level", str(ptt_active_level)])
        if ptt_debounce_ms is not None:
            command.extend(["--ptt-debounce-ms", str(ptt_debounce_ms)])
        if ptt_pull:
            command.extend(["--ptt-pull", ptt_pull])
        if audio_out_enabled is True:
            command.append("--enable-audio-out")
        elif audio_out_enabled is False:
            command.append("--disable-audio-out")
        if audio_out_volume is not None:
            command.extend(["--audio-out-volume", str(audio_out_volume)])
        if tts_enabled is True:
            command.append("--enable-tts")
        elif tts_enabled is False:
            command.append("--disable-tts")
        if tts_max_bytes is not None:
            command.extend(["--tts-max-bytes", str(tts_max_bytes)])
        if led_self_test is True:
            command.append("--enable-led-self-test")
        elif led_self_test is False:
            command.append("--disable-led-self-test")
        if display_enabled is True:
            command.append("--enable-display")
        elif display_enabled is False:
            command.append("--disable-display")
        if display_self_test is True:
            command.append("--enable-display-self-test")
        elif display_self_test is False:
            command.append("--disable-display-self-test")
        result = subprocess.run(
            command,
            cwd=str(V3_DIR),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(detail or f"configure_firmware.py exited {result.returncode}")

        return {
            "ok": True,
            "ssid": ssid,
            "bssid": bssid or None,
            "password_set": True,
            "ptt_gpio": ptt_gpio,
            "ptt_active_level": ptt_active_level,
            "ptt_debounce_ms": ptt_debounce_ms,
            "ptt_pull": ptt_pull or None,
            "audio_out_enabled": audio_out_enabled,
            "audio_out_volume": audio_out_volume,
            "tts_enabled": tts_enabled,
            "tts_max_bytes": tts_max_bytes,
            "led_self_test": led_self_test,
            "display_enabled": display_enabled,
            "display_self_test": display_self_test,
            "message": "Updated ignored firmware/sdkconfig. Rebuild and flash firmware for changes to take effect.",
        }


def parse_llm_response(raw: str) -> tuple[str, str]:
    stripped = strip_markdown_fence(raw.strip())
    json_response = parse_json_llm_response(stripped)
    if json_response:
        return json_response
    json_response = parse_embedded_json_llm_response(stripped)
    if json_response:
        return json_response

    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if not lines:
        return "BS", stripped

    first = normalize_labeled_value(lines[0])
    if first.upper() in LED_COMMANDS:
        return first.upper(), clean_reply("\n".join(lines[1:])) or stripped

    match = re.search(r"\b(GS|GP|GC|RS|RF|YP|BS|PS|PP)\b", stripped.upper())
    if match:
        command = match.group(1)
        cleaned = clean_reply(re.sub(r"\b(GS|GP|GC|RS|RF|YP|BS|PS|PP)\b", "", stripped, count=1))
        cleaned = re.sub(r"\b(?:led|command|code)\s*[:=-]\s*$", "", cleaned, flags=re.IGNORECASE).strip()
        return command, cleaned or stripped

    return "BS", stripped


def normalize_led_command(raw: str) -> str:
    command = raw.strip().upper()
    if command not in LED_COMMANDS:
        raise ValueError(f"Invalid LED command: {raw}")
    return command


def strip_markdown_fence(raw: str) -> str:
    match = re.fullmatch(r"```(?:json|text)?\s*(.*?)\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else raw


def parse_json_llm_response(raw: str) -> tuple[str, str] | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    command = str(payload.get("command") or payload.get("code") or "").strip().upper()
    if command not in LED_COMMANDS:
        return None

    reply = str(payload.get("reply") or payload.get("answer") or payload.get("text") or "").strip()
    return command, reply or raw


def parse_embedded_json_llm_response(raw: str) -> tuple[str, str] | None:
    start = raw.find("{")
    while start >= 0:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(raw)):
            ch = raw[index]
            if escape:
                escape = False
                continue
            if ch == "\\" and in_string:
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    parsed = parse_json_llm_response(raw[start : index + 1])
                    if parsed:
                        return parsed
                    break
        start = raw.find("{", start + 1)
    return None


def normalize_labeled_value(raw: str) -> str:
    return re.sub(r"^\s*(?:led|command|code)\s*[:=-]\s*", "", raw, flags=re.IGNORECASE).strip()


def clean_reply(raw: str) -> str:
    cleaned_lines: list[str] = []
    for line in raw.splitlines():
        cleaned = re.sub(r"^\s*(?:reply|answer|text)\s*[:=-]\s*", "", line, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"^\s*(?:led|command|code)\s*[:=-]\s*$", "", cleaned, flags=re.IGNORECASE).strip()
        if cleaned:
            cleaned_lines.append(cleaned)
    cleaned = "\n".join(cleaned_lines).strip()
    return re.sub(r"^(?:led|command|code)\s*[:=-]\s*", "", cleaned, flags=re.IGNORECASE).strip()


def parse_command_sequence(raw: str) -> list[str]:
    commands: list[str] = []
    for item in raw.split(","):
        if not item.strip():
            continue
        commands.append(normalize_led_command(item))
    return commands


def optional_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("1", "true", "yes", "on"):
            return True
        if normalized in ("0", "false", "no", "off"):
            return False
    raise ValueError("optional config flags must be boolean")


def json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True).encode("utf-8")


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


def make_handler(state: BridgeState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "WearabLLMBridge/0.1"

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path == "/health":
                self._send_json(
                    {
                        "ok": True,
                        "service": "wearabllm-bridge",
                        "config": state.runtime_config(),
                    }
                )
                return
            if path == "/v1/admin/config":
                if not self._is_authorized():
                    self._send_error_json(HTTPStatus.UNAUTHORIZED, "Invalid or missing device token")
                    return
                self._send_json({"ok": True, "config": state.agent_config.snapshot().public_dict()})
                return
            if path == "/v1/admin/catalog":
                if not self._is_authorized():
                    self._send_error_json(HTTPStatus.UNAUTHORIZED, "Invalid or missing device token")
                    return
                try:
                    self._send_json({"ok": True, "catalog": state.openai_catalog()})
                except Exception as exc:
                    self._send_error_json(HTTPStatus.BAD_GATEWAY, str(exc))
                return
            if path == "/v1/interactions":
                if not self._is_authorized():
                    self._send_error_json(HTTPStatus.UNAUTHORIZED, "Invalid or missing device token")
                    return
                params = urllib.parse.parse_qs(parsed.query)
                target = (params.get("target_device_id") or [""])[0].strip()
                limit_raw = (params.get("limit") or ["100"])[0]
                if not limit_raw.isdecimal():
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid limit")
                    return
                try:
                    actions = state.action_queue.list(target_device_id=target, limit=int(limit_raw))
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self._send_json({"ok": True, "actions": actions})
                return
            action_match = re.fullmatch(r"/v1/interactions/([a-f0-9-]{36})", path)
            if action_match:
                if not self._is_authorized():
                    self._send_error_json(HTTPStatus.UNAUTHORIZED, "Invalid or missing device token")
                    return
                try:
                    action = state.action_queue.get(action_match.group(1))
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                if not action:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "Action not found")
                    return
                self._send_json({"ok": True, "action": action})
                return
            device_action_match = re.fullmatch(r"/v1/devices/([A-Za-z0-9._-]{1,80})/actions", path)
            if device_action_match:
                if not self._is_authorized():
                    self._send_error_json(HTTPStatus.UNAUTHORIZED, "Invalid or missing device token")
                    return
                target = device_action_match.group(1)
                try:
                    if self._device_id() != target:
                        self._send_error_json(HTTPStatus.FORBIDDEN, "Device ID does not match action target")
                        return
                    state.touch_device(target)
                    action = state.action_queue.claim_next(target)
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self._send_json({"ok": True, "action": action})
                return
            if path in {"/v1/conversation", "/v1/devices", "/v1/conversation/sessions"}:
                if not self._is_authorized():
                    self._send_error_json(HTTPStatus.UNAUTHORIZED, "Invalid or missing device token")
                    return
                params = urllib.parse.parse_qs(parsed.query)
                if path == "/v1/devices":
                    snapshot = state.conversation_snapshot()
                    self._send_json({"ok": True, "devices": snapshot["devices"]})
                    return
                if path == "/v1/conversation/sessions":
                    snapshot = state.conversation_snapshot()
                    self._send_json(
                        {
                            "ok": True,
                            "active_session_id": snapshot.get("active_session_id"),
                            "sessions": snapshot.get("sessions", []),
                        }
                    )
                    return
                device_id = (params.get("device_id") or [""])[0].strip() or None
                session_id = (params.get("session_id") or [""])[0].strip() or None
                limit_raw = (params.get("limit") or ["200"])[0]
                if not limit_raw.isdecimal():
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid limit")
                    return
                try:
                    snapshot = state.conversation_snapshot(
                        device_id=device_id,
                        session_id=session_id,
                        limit=int(limit_raw),
                    )
                except Exception as exc:
                    print(f"ERROR: conversation snapshot failed: {exc}")
                    self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                    return
                self._send_json(snapshot)
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, "Unknown endpoint")

        def do_OPTIONS(self) -> None:
            self.send_response(HTTPStatus.NO_CONTENT)
            self._send_cors_headers()
            self.end_headers()

        def do_POST(self) -> None:
            if not self._is_authorized():
                self._send_error_json(HTTPStatus.UNAUTHORIZED, "Invalid or missing device token")
                return
            path = urllib.parse.urlsplit(self.path).path
            if path == "/v1/query":
                self._handle_audio_query()
                return
            if path == "/v1/query_text":
                self._handle_text_query()
                return
            if path == "/v1/tts":
                self._handle_tts()
                return
            if path == "/v1/heartbeat":
                try:
                    device_id = self._device_id()
                    state.touch_device(device_id)
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self._send_json({"ok": True, "device_id": device_id})
                return
            if path == "/v1/session/reset":
                try:
                    payload = state.start_new_conversation()
                except Exception as exc:
                    print(f"ERROR: {exc}")
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                self._send_json(payload)
                return
            session_archive_match = re.fullmatch(
                r"/v1/conversation/sessions/([a-f0-9-]{36})/archive", path
            )
            if session_archive_match:
                try:
                    payload = state.archive_conversation(session_archive_match.group(1))
                except LookupError as exc:
                    self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                    return
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                except Exception as exc:
                    print(f"ERROR: conversation archive failed: {exc}")
                    self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                    return
                self._send_json(payload)
                return
            session_rename_match = re.fullmatch(
                r"/v1/conversation/sessions/([a-f0-9-]{36})/rename", path
            )
            if session_rename_match:
                request = self._read_json_request(max_bytes=2_048)
                if request is None:
                    return
                try:
                    payload = state.rename_conversation(
                        session_rename_match.group(1), str(request.get("title", ""))
                    )
                except LookupError as exc:
                    self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                    return
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                except Exception as exc:
                    print(f"ERROR: conversation rename failed: {exc}")
                    self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                    return
                self._send_json(payload)
                return
            if path == "/v1/device_wifi":
                self._handle_device_wifi()
                return
            if path == "/v1/interactions":
                self._handle_interaction()
                return
            if path == "/v1/admin/config":
                self._handle_admin_config()
                return
            if path == "/v1/admin/api-key":
                self._handle_admin_api_key()
                return
            action_ack_match = re.fullmatch(
                r"/v1/devices/([A-Za-z0-9._-]{1,80})/actions/([a-f0-9-]{36})/ack", path
            )
            if action_ack_match:
                self._handle_action_ack(action_ack_match.group(1), action_ack_match.group(2))
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, "Unknown endpoint")

        def _is_authorized(self) -> bool:
            expected = str(getattr(state.args, "device_token", ""))
            if not expected:
                return True
            supplied = self.headers.get("X-WearabLLM-Device-Token", "")
            return bool(supplied) and hmac.compare_digest(supplied, expected)

        def _device_id(self) -> str:
            device_id = self.headers.get("X-WearabLLM-Device-Id", "").strip()
            if not device_id:
                device_id = str(getattr(state.args, "device_id", "wearabllm-unknown"))
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", device_id):
                raise ValueError("Invalid device ID")
            return device_id

        def _handle_audio_query(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Missing audio body")
                return
            if length > state.args.max_audio_bytes:
                self._send_error_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    f"Audio body too large: {length} bytes > {state.args.max_audio_bytes} byte limit",
                )
                return

            wav_bytes = self.rfile.read(length)
            try:
                device_id = self._device_id()
                state.touch_device(device_id)
                saved_path = state.save_debug_wav(wav_bytes)
                wav_info = inspect_wav(wav_bytes)
                transcript = state.transcribe(wav_bytes)
                payload = state.answer_transcript(
                    transcript,
                    device_id=device_id,
                    audio_bytes=len(wav_bytes),
                    saved_wav=saved_path,
                    wav_info=wav_info,
                )
                state.record_capture(
                    wav_bytes=len(wav_bytes),
                    saved_wav=saved_path,
                    wav_info=wav_info,
                    transcript=transcript,
                    command=str(payload.get("command", "")),
                )
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - runtime path
                print(f"ERROR: {exc}")
                self._send_json(
                    {"command": "RF", "reply": f"Bridge error: {exc}", "transcript": ""},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return

            self._log_response(payload)
            if saved_path:
                print(f"Saved WAV : {saved_path}")
            if payload.get("wav_info"):
                print(f"WAV info  : {json.dumps(payload['wav_info'], sort_keys=True)}")
            self._send_json(payload)

        def _handle_text_query(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Missing JSON body")
                return

            raw = self.rfile.read(length)
            try:
                request = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid JSON body")
                return

            transcript = str(request.get("transcript", "")).strip()
            if not transcript:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Missing transcript")
                return

            try:
                response_device = str(request.get("response_device_id", "")).strip() or None
                if response_device:
                    response_device = validate_device_id(response_device)
                device_id = self._device_id()
                state.touch_device(device_id)
                payload = state.answer_transcript(
                    transcript,
                    device_id=device_id,
                    response_device_id=response_device,
                )
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - runtime path
                print(f"ERROR: {exc}")
                self._send_json(
                    {"command": "RF", "reply": f"Bridge error: {exc}", "transcript": transcript},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return

            self._log_response(payload)
            self._send_json(payload)

        def _handle_interaction(self) -> None:
            request = self._read_json_request(max_bytes=16_384)
            if request is None:
                return
            transcript = str(request.get("transcript", "")).strip()
            origin = str(request.get("origin_device_id", "")).strip()
            target = str(request.get("target_device_id", "")).strip()
            if not origin:
                try:
                    origin = self._device_id()
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                    return
            if not transcript or not target:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "transcript and target_device_id are required")
                return
            try:
                state.touch_device(origin)
                payload = state.create_interaction(
                    transcript=transcript,
                    origin_device_id=origin,
                    target_device_id=target,
                    idempotency_key=str(request.get("idempotency_key", "")),
                    response_device_id=str(request.get("response_device_id", "")).strip() or None,
                )
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - runtime path
                print(f"ERROR: interaction creation failed: {exc}")
                self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._log_response(payload)
            self._send_json({"ok": True, **payload})

        def _handle_action_ack(self, target_device_id: str, action_id: str) -> None:
            try:
                if self._device_id() != target_device_id:
                    self._send_error_json(HTTPStatus.FORBIDDEN, "Device ID does not match action target")
                    return
                state.touch_device(target_device_id)
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            request = self._read_json_request(max_bytes=2_048)
            if request is None:
                return
            try:
                action = state.action_queue.acknowledge(
                    target_device_id,
                    action_id,
                    str(request.get("status", "")),
                    str(request.get("error", "")),
                )
            except LookupError:
                self._send_error_json(HTTPStatus.NOT_FOUND, "Action not found")
                return
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json({"ok": True, "action": action})

        def _handle_admin_config(self) -> None:
            request = self._read_json_request(max_bytes=64_000)
            if request is None:
                return
            try:
                config = state.agent_config.update(request)
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - persistence/runtime path
                print(f"ERROR: agent config update failed: {exc}")
                self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json({"ok": True, "config": config.public_dict()})

        def _handle_admin_api_key(self) -> None:
            request = self._read_json_request(max_bytes=2_048)
            if request is None:
                return
            try:
                payload = state.replace_openai_api_key(str(request.get("api_key", "")))
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:
                print(f"ERROR: OpenAI API key update failed: {exc}")
                self._send_error_json(HTTPStatus.BAD_GATEWAY, str(exc))
                return
            self._send_json(payload)

        def _read_json_request(self, *, max_bytes: int) -> dict[str, Any] | None:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > max_bytes:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid JSON body")
                return None
            try:
                request = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid JSON body")
                return None
            if not isinstance(request, dict):
                self._send_error_json(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
                return None
            return request

        def _handle_tts(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Missing JSON body")
                return

            raw = self.rfile.read(length)
            try:
                request = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid JSON body")
                return

            text = str(request.get("text", "")).strip()
            if not text:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Missing text")
                return

            try:
                wav_bytes = state.synthesize_tts_wav(text)
            except Exception as exc:  # pragma: no cover - runtime path
                print(f"ERROR: {exc}")
                self._send_json(
                    {"error": f"Bridge TTS error: {exc}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return

            print(f"TTS text  : {text}")
            print(f"TTS bytes : {len(wav_bytes)}")
            self._send_bytes(wav_bytes, content_type="audio/wav")

        def _handle_device_wifi(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Missing JSON body")
                return

            raw = self.rfile.read(length)
            try:
                request = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid JSON body")
                return

            ssid = str(request.get("ssid", "")).strip()
            password = str(request.get("password", ""))
            bssid = str(request.get("bssid", "")).strip()
            ptt_gpio = request.get("ptt_gpio")
            ptt_active_level = request.get("ptt_active_level")
            ptt_debounce_ms = request.get("ptt_debounce_ms")
            ptt_pull = str(request.get("ptt_pull", "")).strip()
            audio_out_volume = request.get("audio_out_volume")
            tts_max_bytes = request.get("tts_max_bytes")
            try:
                audio_out_enabled = optional_bool(request.get("audio_out_enabled"))
                tts_enabled = optional_bool(request.get("tts_enabled"))
                led_self_test = optional_bool(request.get("led_self_test"))
                display_enabled = optional_bool(request.get("display_enabled"))
                display_self_test = optional_bool(request.get("display_self_test"))
                payload = state.configure_device_wifi(
                    ssid,
                    password,
                    bssid,
                    int(ptt_gpio) if ptt_gpio not in (None, "") else None,
                    int(ptt_active_level) if ptt_active_level not in (None, "") else None,
                    int(ptt_debounce_ms) if ptt_debounce_ms not in (None, "") else None,
                    ptt_pull,
                    audio_out_enabled,
                    int(audio_out_volume) if audio_out_volume not in (None, "") else None,
                    tts_enabled,
                    int(tts_max_bytes) if tts_max_bytes not in (None, "") else None,
                    led_self_test,
                    display_enabled,
                    display_self_test,
                )
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                return
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            except Exception as exc:  # pragma: no cover - runtime path
                print(f"ERROR: {exc}")
                self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            print(f"Device Wi-Fi config updated for SSID: {ssid}")
            self._send_json(payload)

        def _log_response(self, payload: dict[str, Any]) -> None:
            print(f"Transcript: {payload.get('transcript', '')}")
            print(f"Command   : {payload.get('command', '')}")
            print(f"Reply     : {payload.get('reply', '')}")

        def log_message(self, fmt: str, *args: Any) -> None:
            print("%s - %s" % (self.address_string(), fmt % args))

        def _send_cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-WearabLLM-Device-Token, X-WearabLLM-Device-Id")

        def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json_bytes(payload)
            self.send_response(status)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error_json(self, status: HTTPStatus, message: str) -> None:
            self._send_json({"error": message}, status=status)

        def _send_bytes(
            self,
            body: bytes,
            *,
            content_type: str,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            self.send_response(status)
            self._send_cors_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


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

    state = BridgeState(args)
    handler = make_handler(state)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"WearabLLM bridge listening on http://{args.host}:{args.port}")
    print(f"Runtime config: {json.dumps(state.runtime_config(), sort_keys=True)}")
    print(f"POST audio/wav to http://<this-computer-ip>:{args.port}/v1/query")
    print(f"POST JSON transcript to http://<this-computer-ip>:{args.port}/v1/query_text")
    print(f"POST JSON text to http://<this-computer-ip>:{args.port}/v1/tts for audio/wav")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBridge stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
