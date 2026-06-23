from __future__ import annotations

import json
import struct
import tempfile
import threading
import unittest
import wave
from io import BytesIO
from argparse import Namespace
from http.client import HTTPConnection
from pathlib import Path
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch

import wearabllm_bridge
from wearabllm_bridge import (
    BridgeState,
    DEFAULT_MAX_AUDIO_BYTES,
    inspect_wav,
    json_bytes,
    make_silence_wav,
    make_handler,
    normalize_tts_wav,
    parse_command_sequence,
    parse_llm_response,
    pcm16_level_stats,
    TTS_INSTRUCTIONS,
)


class ParseLLMResponseTest(unittest.TestCase):
    def test_parses_expected_two_line_response(self):
        command, reply = parse_llm_response("GC\nYes, absolutely.")
        self.assertEqual(command, "GC")
        self.assertEqual(reply, "Yes, absolutely.")

    def test_parses_labeled_two_line_response(self):
        command, reply = parse_llm_response("LED: YP\nReply: Maybe, test it once more.")
        self.assertEqual(command, "YP")
        self.assertEqual(reply, "Maybe, test it once more.")

    def test_parses_json_response(self):
        command, reply = parse_llm_response('{"command":"PS","reply":"That is a good creative branch."}')
        self.assertEqual(command, "PS")
        self.assertEqual(reply, "That is a good creative branch.")

    def test_parses_fenced_json_response(self):
        command, reply = parse_llm_response('```json\n{"code":"RF","answer":"Stop and check power."}\n```')
        self.assertEqual(command, "RF")
        self.assertEqual(reply, "Stop and check power.")

    def test_parses_embedded_json_response(self):
        command, reply = parse_llm_response('Here is the result:\n{"command":"GP","reply":"Yes, keep going gently."}\nDone.')
        self.assertEqual(command, "GP")
        self.assertEqual(reply, "Yes, keep going gently.")

    def test_parses_embedded_json_with_brace_in_string(self):
        command, reply = parse_llm_response('Result: {"command":"BS","reply":"Use {braces} only in code."}')
        self.assertEqual(command, "BS")
        self.assertEqual(reply, "Use {braces} only in code.")

    def test_parses_embedded_command(self):
        command, reply = parse_llm_response("I would be careful here. LED: RF")
        self.assertEqual(command, "RF")
        self.assertIn("careful", reply)
        self.assertNotIn("LED:", reply)

    def test_falls_back_to_blue_solid_for_unparseable_response(self):
        command, reply = parse_llm_response("I do not know how to classify this.")
        self.assertEqual(command, "BS")
        self.assertEqual(reply, "I do not know how to classify this.")

    def test_empty_response_falls_back(self):
        command, reply = parse_llm_response("")
        self.assertEqual(command, "BS")
        self.assertEqual(reply, "")


class BridgeStateTest(unittest.TestCase):
    def test_typed_transcript_bypasses_stt(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="hello from test",
                stt="openai",
                dry_run=False,
                dry_run_sequence="",
                save_wav_dir="",
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        self.assertEqual(state.transcribe(b"not audio"), "hello from test")

    def test_dry_run_audio_bypasses_stt(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                dry_run=True,
                dry_run_sequence="",
                save_wav_dir="",
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        self.assertIn("dry-run audio upload", state.transcribe(make_silence_wav(125)))

    def test_openai_transcription_uses_supported_upload_tuple(self):
        create = Mock(return_value="hello from microphone")
        state = BridgeState.__new__(BridgeState)
        state.args = Namespace(
            typed="",
            dry_run=False,
            stt="openai",
            stt_model="gpt-4o-transcribe",
        )
        state.openai_client = SimpleNamespace(
            audio=SimpleNamespace(
                transcriptions=SimpleNamespace(create=create),
            )
        )

        wav_bytes = make_silence_wav(125)
        self.assertEqual(state.transcribe(wav_bytes), "hello from microphone")
        upload = create.call_args.kwargs["file"]
        self.assertEqual(upload, ("wearabllm-capture.wav", wav_bytes, "audio/wav"))

    def test_save_debug_wav_writes_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = BridgeState(
                Namespace(
                    provider="openai",
                    typed="",
                    stt="openai",
                    dry_run=False,
                    dry_run_sequence="",
                    save_wav_dir=tmpdir,
                    max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
                )
            )
            path = state.save_debug_wav(b"RIFF-test")
            self.assertIsNotNone(path)
            assert path is not None
            self.assertEqual(Path(path).read_bytes(), b"RIFF-test")

    def test_answer_transcript_uses_shared_response_shape(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        payload = state.answer_transcript("should I test the bridge?", audio_bytes=12)
        self.assertEqual(payload["command"], "BS")
        self.assertEqual(payload["transcript"], "should I test the bridge?")
        self.assertEqual(payload["audio_bytes"], 12)
        self.assertIsNone(payload["saved_wav"])
        self.assertIsNone(payload["wav_info"])
        self.assertIn("Dry run transcript", payload["reply"])

    def test_answer_transcript_can_include_wav_info(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        wav_info = {"valid": True, "sample_rate": 16000}
        payload = state.answer_transcript("audio test", audio_bytes=44, wav_info=wav_info)
        self.assertEqual(payload["wav_info"], wav_info)

    def test_dry_run_tts_returns_wav(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                tts_model="test-model",
                tts_voice="alloy",
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        wav_bytes = state.synthesize_tts_wav("hello from tts")
        self.assertTrue(wav_bytes.startswith(b"RIFF"))
        with wave.open(BytesIO(wav_bytes), "rb") as wav_file:
            self.assertEqual(wav_file.getframerate(), 16000)
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.getsampwidth(), 2)
            self.assertGreater(wav_file.getnframes(), 0)

    def test_live_tts_uses_verse_and_theatrical_instructions(self):
        wav_bytes = make_silence_wav(125)
        create = Mock(return_value=SimpleNamespace(read=lambda: wav_bytes))
        state = BridgeState.__new__(BridgeState)
        state.args = Namespace(
            provider="openai",
            dry_run=False,
            tts_model="gpt-4o-mini-tts",
            tts_voice="verse",
            tts_instructions=TTS_INSTRUCTIONS,
        )
        state.openai_client = SimpleNamespace(
            audio=SimpleNamespace(speech=SimpleNamespace(create=create))
        )

        result = state.synthesize_tts_wav("The experiment is alive!")

        self.assertTrue(result.startswith(b"RIFF"))
        self.assertEqual(create.call_args.kwargs["voice"], "verse")
        self.assertEqual(create.call_args.kwargs["instructions"], TTS_INSTRUCTIONS)
        self.assertEqual(create.call_args.kwargs["response_format"], "wav")

    def test_normalize_tts_wav_resamples_and_rewrites_header(self):
        with BytesIO() as source:
            with wave.open(source, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(24000)
                wav_file.writeframes(struct.pack("<h", 1000) * 2400)
            normalized = normalize_tts_wav(source.getvalue())

        with wave.open(BytesIO(normalized), "rb") as wav_file:
            self.assertEqual(wav_file.getframerate(), 16000)
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.getsampwidth(), 2)
            self.assertEqual(wav_file.getnframes(), 1600)

    def test_runtime_config_reports_live_bridge_settings(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="bench test",
                stt="local-whisper",
                stt_model="gpt-4o-transcribe",
                llm_model="gpt-5.4-mini",
                tts_model="gpt-4o-mini-tts",
                tts_voice="alloy",
                save_wav_dir="./captures",
                allow_device_config=True,
                dry_run=True,
                dry_run_command="RF",
                dry_run_sequence="GS,RF",
                max_audio_bytes=123456,
            )
        )
        config = state.runtime_config()
        self.assertEqual(config["provider"], "openai")
        self.assertEqual(config["stt"], "local-whisper")
        self.assertEqual(config["llm_model"], "gpt-5.4-mini")
        self.assertTrue(config["dry_run"])
        self.assertEqual(config["dry_run_command"], "RF")
        self.assertEqual(config["dry_run_sequence"], ["GS", "RF"])
        self.assertTrue(config["device_config"])
        self.assertTrue(config["typed_bypass"])
        self.assertEqual(config["save_wav_dir"], "./captures")
        self.assertEqual(config["max_audio_bytes"], 123456)
        self.assertEqual(config["capture_count"], 0)
        self.assertIsNone(config["latest_capture"])
        self.assertIn("firmware_config", config)

    def test_runtime_config_reports_latest_audio_capture(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                stt_model="gpt-4o-transcribe",
                llm_model="gpt-5.4-mini",
                tts_model="gpt-4o-mini-tts",
                tts_voice="alloy",
                save_wav_dir="",
                allow_device_config=False,
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        state.record_capture(
            wav_bytes=32044,
            saved_wav=None,
            wav_info={"valid": True, "duration_ms": 1000, "appears_silent": False},
            transcript="hello hardware",
            command="GS",
        )
        config = state.runtime_config()
        self.assertEqual(config["capture_count"], 0)
        latest = config["latest_capture"]
        self.assertIsInstance(latest, dict)
        self.assertEqual(latest["audio_bytes"], 32044)
        self.assertEqual(latest["transcript_len"], len("hello hardware"))
        self.assertEqual(latest["command"], "GS")
        self.assertEqual(latest["wav_info"]["duration_ms"], 1000)

    def test_firmware_config_status_reads_config_helper_json(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                allow_device_config=True,
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        with tempfile.NamedTemporaryFile() as helper:
            helper_path = Path(helper.name)
            with patch.object(wearabllm_bridge, "CONFIGURE_FIRMWARE", helper_path), patch(
                "wearabllm_bridge.subprocess.run"
            ) as run:
                run.return_value.returncode = 0
                run.return_value.stdout = json.dumps(
                    {
                        "ready": True,
                        "wifi_ssid_set": True,
                        "led_self_test": True,
                        "display_enabled": True,
                        "display_self_test": False,
                    }
                )
                run.return_value.stderr = ""

                payload = state.firmware_config_status()

        args = run.call_args.args[0]
        self.assertEqual(args, [str(helper_path), "--status-json"])
        self.assertTrue(payload["available"])
        self.assertTrue(payload["ready"])
        self.assertTrue(payload["led_self_test"])
        self.assertTrue(payload["display_enabled"])
        self.assertFalse(payload["display_self_test"])

    def test_device_wifi_config_is_opt_in(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                allow_device_config=False,
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        with self.assertRaises(PermissionError):
            state.configure_device_wifi("ssid", "password")

    def test_device_wifi_config_requires_ssid_and_password_when_enabled(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                allow_device_config=True,
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        with self.assertRaises(ValueError):
            state.configure_device_wifi("", "password", "02:00:00:00:00:01")

    def test_device_config_validates_ptt_options(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                allow_device_config=True,
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        with self.assertRaises(ValueError):
            state.configure_device_wifi("ssid", "password", ptt_active_level=2)
        with self.assertRaises(ValueError):
            state.configure_device_wifi("ssid", "password", ptt_debounce_ms=251)
        with self.assertRaises(ValueError):
            state.configure_device_wifi("ssid", "password", ptt_pull="sideways")
        with self.assertRaises(ValueError):
            state.configure_device_wifi("ssid", "password", audio_out_volume=101)
        with self.assertRaises(ValueError):
            state.configure_device_wifi("ssid", "password", tts_max_bytes=4095)

    def test_optional_bool_parses_display_flags(self):
        self.assertIsNone(wearabllm_bridge.optional_bool(None))
        self.assertIsNone(wearabllm_bridge.optional_bool(""))
        self.assertTrue(wearabllm_bridge.optional_bool(True))
        self.assertTrue(wearabllm_bridge.optional_bool("yes"))
        self.assertFalse(wearabllm_bridge.optional_bool(False))
        self.assertFalse(wearabllm_bridge.optional_bool("off"))
        with self.assertRaises(ValueError):
            wearabllm_bridge.optional_bool("maybe")

    def test_device_config_forwards_hardware_options_to_configure_helper(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                allow_device_config=True,
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        with tempfile.NamedTemporaryFile() as helper:
            helper_path = Path(helper.name)
            with patch.object(wearabllm_bridge, "CONFIGURE_FIRMWARE", helper_path), patch(
                "wearabllm_bridge.subprocess.run"
            ) as run:
                run.return_value.returncode = 0
                run.return_value.stdout = ""
                run.return_value.stderr = ""

                payload = state.configure_device_wifi(
                    "ssid",
                    "password",
                    "02:00:00:00:00:01",
                    ptt_gpio=8,
                    ptt_active_level=1,
                    ptt_debounce_ms=45,
                    ptt_pull="down",
                    audio_out_enabled=True,
                    audio_out_volume=55,
                    tts_enabled=True,
                    tts_max_bytes=65536,
                    led_self_test=True,
                    display_enabled=True,
                    display_self_test=True,
                )

        args = run.call_args.args[0]
        self.assertEqual(args[0], str(helper_path))
        self.assertIn("--ptt-gpio", args)
        self.assertIn("--ptt-active-level", args)
        self.assertIn("--ptt-debounce-ms", args)
        self.assertIn("--ptt-pull", args)
        self.assertIn("--enable-audio-out", args)
        self.assertIn("--audio-out-volume", args)
        self.assertIn("--enable-tts", args)
        self.assertIn("--tts-max-bytes", args)
        self.assertIn("--enable-led-self-test", args)
        self.assertIn("--enable-display", args)
        self.assertIn("--enable-display-self-test", args)
        self.assertEqual(payload["ptt_gpio"], 8)
        self.assertEqual(payload["ptt_active_level"], 1)
        self.assertEqual(payload["ptt_debounce_ms"], 45)
        self.assertEqual(payload["ptt_pull"], "down")
        self.assertIs(payload["audio_out_enabled"], True)
        self.assertEqual(payload["audio_out_volume"], 55)
        self.assertIs(payload["tts_enabled"], True)
        self.assertEqual(payload["tts_max_bytes"], 65536)
        self.assertIs(payload["led_self_test"], True)
        self.assertIs(payload["display_enabled"], True)
        self.assertIs(payload["display_self_test"], True)

    def test_device_config_forwards_display_disable_options(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                allow_device_config=True,
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        with tempfile.NamedTemporaryFile() as helper:
            helper_path = Path(helper.name)
            with patch.object(wearabllm_bridge, "CONFIGURE_FIRMWARE", helper_path), patch(
                "wearabllm_bridge.subprocess.run"
            ) as run:
                run.return_value.returncode = 0
                run.return_value.stdout = ""
                run.return_value.stderr = ""

                payload = state.configure_device_wifi(
                    "ssid",
                    "password",
                    audio_out_enabled=False,
                    tts_enabled=False,
                    led_self_test=False,
                    display_enabled=False,
                    display_self_test=False,
                )

        args = run.call_args.args[0]
        self.assertIn("--disable-audio-out", args)
        self.assertIn("--disable-tts", args)
        self.assertIn("--disable-led-self-test", args)
        self.assertIn("--disable-display", args)
        self.assertIn("--disable-display-self-test", args)
        self.assertIs(payload["audio_out_enabled"], False)
        self.assertIs(payload["tts_enabled"], False)
        self.assertIs(payload["led_self_test"], False)
        self.assertIs(payload["display_enabled"], False)
        self.assertIs(payload["display_self_test"], False)

    def test_dry_run_command_override_controls_response_code(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="GC",
                dry_run_sequence="",
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        command, reply = state.ask_llm("test command override")
        self.assertEqual(command, "GC")
        self.assertIn("test command override", reply)

    def test_invalid_dry_run_command_is_rejected(self):
        with self.assertRaises(ValueError):
            BridgeState(
                Namespace(
                    provider="openai",
                    typed="",
                    stt="openai",
                    save_wav_dir="",
                    dry_run=True,
                    dry_run_command="NO",
                    dry_run_sequence="",
                    max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
                )
            )

    def test_dry_run_sequence_cycles_response_codes(self):
        state = BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                save_wav_dir="",
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="GS, RF",
                max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
            )
        )
        self.assertEqual(state.ask_llm("first")[0], "GS")
        self.assertEqual(state.ask_llm("second")[0], "RF")
        self.assertEqual(state.ask_llm("third")[0], "GS")

    def test_live_llm_retains_bounded_session_history(self):
        create = Mock(
            side_effect=[
                SimpleNamespace(output_text="BS\nMy name is WearabLLM."),
                SimpleNamespace(output_text="GP\nYes, I remember."),
            ]
        )
        with patch("wearabllm_bridge.OpenAI") as openai:
            openai.return_value = SimpleNamespace(
                responses=SimpleNamespace(create=create)
            )
            state = BridgeState(
                Namespace(
                    provider="openai", typed="", stt="openai", save_wav_dir="",
                    dry_run=False, dry_run_command="BS", dry_run_sequence="",
                    history_turns=1, llm_model="test-model",
                    max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
                )
            )

        state.ask_llm("What is your name?")
        state.ask_llm("Do you remember?")
        second_input = create.call_args_list[1].kwargs["input"]
        self.assertEqual([message["role"] for message in second_input], ["user", "assistant", "user"])
        self.assertEqual(len(state.history), 2)
        state.clear_history()
        self.assertEqual(state.history, [])

    def test_live_llm_extracts_and_retrieves_durable_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_file = Path(temp_dir) / "memory.json"
            create = Mock(
                side_effect=[
                    SimpleNamespace(output_text="GP\nTea sounds right."),
                    SimpleNamespace(output_text='["The user prefers green tea in the morning."]'),
                    SimpleNamespace(output_text="BS\nYou prefer green tea."),
                    SimpleNamespace(output_text="[]"),
                ]
            )
            with patch("wearabllm_bridge.OpenAI") as openai:
                openai.return_value = SimpleNamespace(
                    responses=SimpleNamespace(create=create)
                )
                state = BridgeState(
                    Namespace(
                        provider="openai", typed="", stt="openai", save_wav_dir="",
                        dry_run=False, dry_run_command="BS", dry_run_sequence="",
                        history_turns=0, llm_model="test-model", memory_model="test-model",
                        durable_memory=True, memory_file=str(memory_file),
                        memory_retrieval_limit=3,
                        max_audio_bytes=DEFAULT_MAX_AUDIO_BYTES,
                    )
                )

            state.ask_llm("I prefer green tea in the morning.")
            self.assertEqual(len(state.memory_store.list()), 1)
            state.ask_llm("What tea do I prefer?")
            self.assertIn(
                "The user prefers green tea in the morning.",
                create.call_args_list[2].kwargs["instructions"],
            )

    def test_parse_command_sequence_rejects_unknown_code(self):
        with self.assertRaises(ValueError):
            parse_command_sequence("GS,NOPE")

    def test_parse_command_sequence_allows_spacing_and_lowercase(self):
        self.assertEqual(parse_command_sequence(" gs, pp "), ["GS", "PP"])


class BridgeHandlerTest(unittest.TestCase):
    def make_state(self) -> BridgeState:
        return BridgeState(
            Namespace(
                provider="openai",
                typed="",
                stt="openai",
                stt_model="gpt-4o-transcribe",
                llm_model="gpt-5.4-mini",
                tts_model="gpt-4o-mini-tts",
                tts_voice="alloy",
                save_wav_dir="",
                allow_device_config=False,
                dry_run=True,
                dry_run_command="BS",
                dry_run_sequence="",
                max_audio_bytes=96,
            )
        )

    def request(self, method: str, path: str, body: bytes = b"", headers: dict[str, str] | None = None) -> tuple[int, dict[str, object]]:
        handler = make_handler(self.make_state())
        handler.log_message = lambda *_args: None  # type: ignore[method-assign]
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            raw = response.read()
            conn.close()
            return response.status, json.loads(raw.decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_missing_transcript_returns_json_error(self):
        status, payload = self.request(
            "POST",
            "/v1/query_text",
            body=b'{"transcript":""}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "Missing transcript")

    def test_unknown_endpoint_returns_json_error(self):
        status, payload = self.request("GET", "/not-found")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "Unknown endpoint")

    def test_oversized_audio_upload_returns_json_error(self):
        status, payload = self.request(
            "POST",
            "/v1/query",
            body=b"0" * 97,
            headers={"Content-Type": "audio/wav"},
        )
        self.assertEqual(status, 413)
        self.assertIn("Audio body too large", str(payload["error"]))


class JsonBytesTest(unittest.TestCase):
    def test_json_bytes_is_ascii_json(self):
        payload = {"command": "PS", "reply": "cool idea"}
        encoded = json_bytes(payload)
        self.assertEqual(json.loads(encoded.decode("ascii")), payload)


class WavTest(unittest.TestCase):
    def test_make_silence_wav_is_valid_16khz_mono(self):
        wav_bytes = make_silence_wav(500)
        self.assertTrue(wav_bytes.startswith(b"RIFF"))
        with wave.open(BytesIO(wav_bytes), "rb") as wav_file:
            self.assertEqual(wav_file.getframerate(), 16000)
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.getsampwidth(), 2)
            self.assertEqual(wav_file.getnframes(), 8000)

    def test_inspect_wav_reports_expected_metadata(self):
        wav_bytes = make_silence_wav(250)
        info = inspect_wav(wav_bytes)
        self.assertTrue(info["valid"])
        self.assertEqual(info["sample_rate"], 16000)
        self.assertEqual(info["channels"], 1)
        self.assertEqual(info["sample_width_bytes"], 2)
        self.assertEqual(info["frames"], 4000)
        self.assertEqual(info["duration_ms"], 250)
        self.assertTrue(info["appears_silent"])
        self.assertEqual(info["peak_abs"], 0)
        self.assertIsNone(info["rms_dbfs"])

    def test_pcm16_level_stats_reports_non_silent_audio(self):
        pcm = struct.pack("<hhhh", 0, 1200, -1200, 0)
        stats = pcm16_level_stats(pcm)
        self.assertEqual(stats["peak_abs"], 1200)
        self.assertFalse(stats["appears_silent"])
        self.assertLess(stats["peak_dbfs"], 0)
        self.assertLess(stats["rms_dbfs"], 0)

    def test_inspect_wav_reports_non_silent_metadata(self):
        with BytesIO() as buffer:
            with wave.open(buffer, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(struct.pack("<hhhh", 0, 1200, -1200, 0))
            info = inspect_wav(buffer.getvalue())
        self.assertTrue(info["valid"])
        self.assertFalse(info["appears_silent"])
        self.assertEqual(info["peak_abs"], 1200)

    def test_inspect_wav_reports_invalid_audio(self):
        info = inspect_wav(b"not a wav")
        self.assertFalse(info["valid"])
        self.assertIn("error", info)


if __name__ == "__main__":
    unittest.main()
