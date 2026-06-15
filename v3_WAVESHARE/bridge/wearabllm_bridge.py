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
import json
import math
import os
import re
import struct
import subprocess
import tempfile
import time
import wave
from io import BytesIO
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

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
Line 2: one short conversational answer, 1-2 sentences max.

Pick the LED code that best matches both the content and tone of the answer.
Do not include markdown. Do not include extra labels.
"""

TTS_SAMPLE_RATE = 16000
TTS_CHANNELS = 1
TTS_SAMPLE_WIDTH = 2
DEFAULT_MAX_AUDIO_BYTES = 512 * 1024
V3_DIR = Path(__file__).resolve().parents[1]
CONFIGURE_FIRMWARE = V3_DIR / "scripts" / "configure_firmware.py"


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
        self.openai_client = OpenAI() if OpenAI and args.provider == "openai" else None
        self.whisper_model: Any | None = None
        self.capture_count = 0
        self.latest_capture: dict[str, Any] | None = None
        self.dry_run_command = normalize_led_command(getattr(args, "dry_run_command", "BS"))
        self.dry_run_sequence = parse_command_sequence(args.dry_run_sequence)
        self.dry_run_sequence_index = 0

    def runtime_config(self) -> dict[str, Any]:
        config = {
            "provider": self.args.provider,
            "dry_run": self.args.dry_run,
            "dry_run_command": self.dry_run_command,
            "dry_run_sequence": self.dry_run_sequence,
            "device_config": bool(getattr(self.args, "allow_device_config", False)),
            "stt": self.args.stt,
            "stt_model": self.args.stt_model,
            "llm_model": self.args.llm_model,
            "tts_model": self.args.tts_model,
            "tts_voice": self.args.tts_voice,
            "typed_bypass": bool(self.args.typed),
            "save_wav_dir": self.args.save_wav_dir or None,
            "capture_count": self.capture_count,
            "latest_capture": self.latest_capture,
            "max_audio_bytes": self.args.max_audio_bytes,
        }
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
            with tempfile.NamedTemporaryFile(suffix=".wav") as audio_file:
                audio_file.write(wav_bytes)
                audio_file.flush()
                audio_file.seek(0)
                result = self.openai_client.audio.transcriptions.create(
                    model=self.args.stt_model,
                    file=audio_file,
                    response_format="text",
                )
            return str(result).strip()

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

    def ask_llm(self, transcript: str) -> tuple[str, str]:
        if self.args.dry_run:
            command = self.next_dry_run_command()
            return command, f"Dry run transcript: {transcript or '(empty audio)'}"

        if self.args.provider != "openai":
            raise RuntimeError(f"Unsupported LLM provider: {self.args.provider}")
        if not self.openai_client:
            raise RuntimeError("openai package is not installed")

        response = self.openai_client.responses.create(
            model=self.args.llm_model,
            instructions=SYSTEM_PROMPT,
            input=transcript,
            max_output_tokens=160,
        )
        raw = str(response.output_text).strip()
        return parse_llm_response(raw)

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
        audio_bytes: int = 0,
        saved_wav: Path | None = None,
        wav_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        command, reply = self.ask_llm(transcript)
        return {
            "command": command,
            "reply": reply,
            "transcript": transcript,
            "audio_bytes": audio_bytes,
            "saved_wav": str(saved_wav) if saved_wav else None,
            "wav_info": wav_info,
        }

    def synthesize_tts_wav(self, text: str) -> bytes:
        if self.args.dry_run:
            return make_silence_wav(milliseconds=max(250, min(2000, len(text) * 35)))

        if self.args.provider != "openai":
            raise RuntimeError(f"Unsupported TTS provider: {self.args.provider}")
        if not self.openai_client:
            raise RuntimeError("openai package is not installed")

        response = self.openai_client.audio.speech.create(
            model=self.args.tts_model,
            voice=self.args.tts_voice,
            input=text,
            response_format="wav",
        )
        if hasattr(response, "read"):
            return bytes(response.read())
        if isinstance(response, bytes):
            return response
        raise RuntimeError("Unexpected TTS response type")

    def configure_device_wifi(
        self,
        ssid: str,
        password: str,
        bssid: str = "",
        ptt_gpio: int | None = None,
        ptt_active_level: int | None = None,
        ptt_debounce_ms: int | None = None,
        ptt_pull: str = "",
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
    raise ValueError("display flags must be boolean")


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


def make_handler(state: BridgeState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "WearabLLMBridge/0.1"

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send_json(
                    {
                        "ok": True,
                        "service": "wearabllm-bridge",
                        "config": state.runtime_config(),
                    }
                )
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, "Unknown endpoint")

        def do_OPTIONS(self) -> None:
            self.send_response(HTTPStatus.NO_CONTENT)
            self._send_cors_headers()
            self.end_headers()

        def do_POST(self) -> None:
            if self.path == "/v1/query":
                self._handle_audio_query()
                return
            if self.path == "/v1/query_text":
                self._handle_text_query()
                return
            if self.path == "/v1/tts":
                self._handle_tts()
                return
            if self.path == "/v1/device_wifi":
                self._handle_device_wifi()
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, "Unknown endpoint")

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
                saved_path = state.save_debug_wav(wav_bytes)
                wav_info = inspect_wav(wav_bytes)
                transcript = state.transcribe(wav_bytes)
                payload = state.answer_transcript(
                    transcript,
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
                payload = state.answer_transcript(transcript)
            except Exception as exc:  # pragma: no cover - runtime path
                print(f"ERROR: {exc}")
                self._send_json(
                    {"command": "RF", "reply": f"Bridge error: {exc}", "transcript": transcript},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return

            self._log_response(payload)
            self._send_json(payload)

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
            try:
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
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

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
    parser.add_argument("--provider", choices=["openai"], default="openai")
    parser.add_argument("--llm-model", default=os.environ.get("WEARABLLM_LLM_MODEL", "gpt-5.4-mini"))
    parser.add_argument("--stt", choices=["openai", "local-whisper"], default=os.environ.get("WEARABLLM_STT", "openai"))
    parser.add_argument("--stt-model", default=os.environ.get("WEARABLLM_STT_MODEL", "gpt-4o-transcribe"))
    parser.add_argument("--tts-model", default=os.environ.get("WEARABLLM_TTS_MODEL", "gpt-4o-mini-tts"))
    parser.add_argument("--tts-voice", default=os.environ.get("WEARABLLM_TTS_VOICE", "alloy"))
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
    if args.provider == "openai" and not args.dry_run and not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required unless --dry-run is set")

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
