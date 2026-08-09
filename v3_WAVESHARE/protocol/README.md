# WearabLLM v3 Protocol

The phase-1 bridge response is JSON over HTTP.

Health/config check:

```http
GET /health
```

Conversation console (token-protected when `WEARABLLM_DEVICE_TOKEN` is set):

```http
GET /v1/conversation
GET /v1/conversation?device_id=wearabllm-esp32&limit=200
GET /v1/conversation/sessions
GET /v1/devices
```

These return the shared principal session turns tagged with `device_id`, so a
home Waveshare, web console, and future wearable can share one thread while
still filtering by body. Replies from the local console use:

```http
POST /v1/query_text
Content-Type: application/json
X-WearabLLM-Device-Token: <device token>
X-WearabLLM-Device-Id: web-console

{"transcript":"continue from the browser"}
```

The response includes the active bridge mode, model settings, upload cap, and
latest audio-capture summary so bench tests can confirm dry-run versus live API
behavior and whether the board has posted audio. When bridge device config is
enabled, it also includes sanitized local firmware `sdkconfig` status for the
next flash:

```json
{
  "ok": true,
  "service": "wearabllm-bridge",
  "config": {
    "dry_run": true,
    "dry_run_command": "BS",
    "stt": "openai",
    "stt_model": "gpt-4o-transcribe",
    "llm_model": "gpt-5.4-mini",
    "tts_model": "gpt-4o-mini-tts",
    "max_audio_bytes": 524288,
    "capture_count": 1,
    "latest_capture": {
      "audio_bytes": 32044,
      "saved_wav": "captures/wearabllm-20260613-120000-001.wav",
      "transcript_len": 29,
      "command": "GS",
      "wav_info": {
        "valid": true,
        "duration_ms": 1000,
        "appears_silent": false
      }
    },
    "firmware_config": {
      "available": true,
      "ready": true,
      "wifi_ssid_set": true,
      "wifi_password_set": true,
      "wifi_bssid": "02:00:00:00:00:01",
      "ptt_gpio": 0,
      "ptt_active_level": 0,
      "ptt_debounce_ms": 35,
      "ptt_pull": "up",
      "audio_out_enabled": false,
      "audio_out_volume": 45,
      "tts_enabled": false,
      "tts_url": "http://192.0.2.10:8765/v1/tts",
      "tts_max_bytes": 131072,
      "led_self_test": true,
      "display_enabled": true,
      "display_self_test": false
    }
  }
}
```

Firmware sends:

```http
POST /v1/query
Content-Type: audio/wav

<16 kHz mono WAV preferred>
```

The bridge smoke script tests this endpoint automatically when the bridge is in dry-run mode:

```bash
cd v3_WAVESHARE
./scripts/bridge_smoke.sh
```

Dry-run bridge mode can also cycle command responses for LED ring bench testing:

```bash
WEARABLLM_DRY_RUN_SEQUENCE=GS,GP,GC,RS,RF,YP,BS,PS,PP ./scripts/run_bridge_dryrun.sh
```

The app and manual tests can send a transcript without audio:

```http
POST /v1/query_text
Content-Type: application/json

{"transcript":"Should I keep testing this wiring?"}
```

Phase-2 TTS scaffolding can request audio for reply text:

```http
POST /v1/tts
Content-Type: application/json

{"text":"Yes, keep testing the display wiring."}
```

The response is:

```http
Content-Type: audio/wav

<16 kHz mono WAV in dry-run mode; provider WAV in live mode>
```

Bench-stage device setup can ask the local bridge to update ignored
`firmware/sdkconfig` for the next flash:

```http
POST /v1/device_wifi
Content-Type: application/json

{"ssid":"example-network","password":"..."}
```

Optional BSSID/AP MAC pinning, PTT wiring settings, speaker/TTS settings, and
TFT bring-up settings are supported:

```json
{
  "ssid": "example-network",
  "password": "...",
  "bssid": "02:00:00:00:00:01",
  "ptt_gpio": 0,
  "ptt_active_level": 0,
  "ptt_debounce_ms": 35,
  "ptt_pull": "up",
  "audio_out_enabled": false,
  "audio_out_volume": 45,
  "tts_enabled": false,
  "tts_max_bytes": 131072,
  "led_self_test": true,
  "display_enabled": true,
  "display_self_test": true
}
```

`ptt_pull` may be `up`, `down`, or `none`.
`tts_enabled` enables `audio_out_enabled` in the firmware config helper.
`display_self_test` enables `display_enabled` in the firmware config helper.

This endpoint is disabled unless the bridge is started with
`--allow-device-config`. The dry-run helper enables it for local bench testing.
It does not provision the running ESP32 over the air; rebuild and flash after a
successful response.

Bridge returns:

```json
{
  "command": "BS",
  "reply": "Short conversational answer.",
  "transcript": "Recognized user speech.",
  "audio_bytes": 123456,
  "saved_wav": null,
  "wav_info": {
    "valid": true,
    "sample_rate": 16000,
    "channels": 1,
    "sample_width_bytes": 2,
    "frames": 32000,
    "duration_ms": 2000,
    "peak_abs": 1200,
    "peak_dbfs": -28.7,
    "rms": 480.4,
    "rms_dbfs": -36.7,
    "appears_silent": false
  }
}
```

The live LLM is instructed to emit a two-line response, but the bridge also
tolerates labeled text (`LED: BS`, `Reply: ...`), small JSON responses
(`{"command":"BS","reply":"..."}`), and JSON objects wrapped in short prose.
It normalizes everything to the response shape above before sending it to
firmware or the Android app.

## Fields

| Field | Type | Required | Used by |
|---|---|---:|---|
| `command` | string | yes | firmware LED ring |
| `reply` | string | yes | firmware serial log, optional TFT reply panel, future TTS |
| `transcript` | string | yes | bridge log, firmware serial log, optional TFT heard panel, app history |
| `audio_bytes` | number | yes | debugging capture/upload |
| `saved_wav` | string or null | yes | bridge-side audio debugging |
| `wav_info` | object or null | yes | bridge-side audio format debugging |

For `/v1/query_text`, `audio_bytes` is `0`, `saved_wav` is `null`, and `wav_info` is `null`.

Error responses from bridge API endpoints are JSON:

```json
{"error":"Missing transcript"}
```

The bridge rejects `/v1/query` uploads above `max_audio_bytes` with HTTP `413`
before reading the body. The default limit is 524288 bytes, comfortably above
the current firmware's 16 kHz mono 16-bit 6-second WAV capture size.

## LED Commands

| Code | Valence | Visual intent |
|---|---|---|
| `GS` | yes, confident | green solid |
| `GP` | yes, gentle | green pulse |
| `GC` | yes, enthusiastic | green chase |
| `RS` | no, firm | red solid |
| `RF` | warning / urgent | red flicker |
| `YP` | uncertain / maybe | yellow pulse |
| `BS` | neutral information | blue solid |
| `PS` | creative / imaginative | purple solid |
| `PP` | deep / philosophical | purple pulse |

## Compatibility Rule

The firmware should ignore unknown extra JSON fields. That lets the bridge and later Android app add fields such as `tts_url`, `tts_bytes`, `display_title`, or `confidence` without breaking the first hardware loop.

The 9-command scale is intentionally duplicated in firmware, bridge, app, and
docs for now. Run this after changing command names or meanings:

```bash
cd v3_WAVESHARE
./scripts/validate_protocol.py
```

Dry-run command overrides and dry-run command sequences are validated against
the same 9-command set at bridge startup.

The `/v1/tts` endpoint is intentionally separate from `/v1/query` for now. The default firmware can keep the LED/display loop stable while speaker playback and TTS transport are tested independently.

When `CONFIG_WEARABLLM_AUDIO_OUT_ENABLED` and `CONFIG_WEARABLLM_TTS_ENABLED` are both enabled, firmware posts the bridge `reply` text to `CONFIG_WEARABLLM_TTS_URL` after a successful `/v1/query` response and attempts to play the returned WAV. TTS fetch/play failures are logged and should not block the LED/display response.
