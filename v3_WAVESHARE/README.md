# v3 Waveshare ESP32-S3 Audio Board

Target workspace for the ESP32-S3-AUDIO-Board iteration.

Phase 1 keeps the v1 WearabLLM response contract:

```text
user speech -> transcript -> LLM -> 2-character LED command + short answer
```

The hardware path changes:

```text
hold device button
  -> white RGB ring while listening
  -> board records audio
  -> HTTP POST audio/wav to local bridge
  -> bridge transcribes speech and asks the LLM
  -> bridge returns {"command":"GS","reply":"..."}
  -> board shows command color on the 7-LED ring
```

## Current Status

Implemented in this folder:

- ESP-IDF scaffold in `firmware/`
- RGB ring setup using Waveshare's documented/demo pin: `GPIO38`, 7 WS2812 LEDs
- hold-to-talk state machine using configurable PTT GPIO, active level, and pull mode
- Wi-Fi station setup
- HTTP client call to the local bridge
- optional bridge-free HTTPS path for OpenAI transcription, Responses, and TTS
- ES7210/I2S microphone capture module using Waveshare's audio codec path
- optional ES8311 speaker-output earcon scaffold behind `CONFIG_WEARABLLM_AUDIO_OUT_ENABLED`
- local Python bridge in `bridge/`
- Android-first Expo companion scaffold in `app/`
- OpenAI STT bridge path using `gpt-4o-transcribe`
- bridge text-query endpoint for app/manual testing at `/v1/query_text`
- opt-in bridge endpoint for app-assisted device Wi-Fi, PTT, RGB, speaker, TTS, and TFT config for the next firmware flash
- app hold-to-speak input through Android native STT, routed to `/v1/query_text`
- phase-2 bridge TTS scaffold at `/v1/tts`
- optional firmware TTS fetch/play scaffold behind `CONFIG_WEARABLLM_TTS_ENABLED`
- bridge dry-run mode and optional received-WAV capture saving
- board-side capture diagnostics in serial logs: duration, peak, RMS, and silence flag
- pre-flash image/config coherence gate with a stricter optional first-flash profile
- LLM valence response parser for `GS`, `GP`, `GC`, `RS`, `RF`, `YP`, `BS`, `PS`, `PP`
- optional ST7735 SPI TFT status/response display driver behind `CONFIG_WEARABLLM_DISPLAY_ENABLED`
- optional TFT boot wiring self-test behind `CONFIG_WEARABLLM_DISPLAY_SELF_TEST_ON_BOOT`
- TFT perfboard adapter diagram: `tft_perfboard_adapter.svg`

Current verification status:

- Firmware builds locally with ESP-IDF v5.5; the current phase-1 image links successfully with the staged Wi-Fi and bridge configuration.
- Firmware has been flashed to the physical Waveshare board over `/dev/cu.usbmodem101`.
- Boot logs confirm ESP32-S3 boot, 16 MB flash, 8 MB PSRAM, app startup, and ES7210 microphone codec initialization.
- Wi-Fi credentials are configured in the local ignored `sdkconfig`, with optional AP MAC/BSSID pinning for the current bench network.
- The full board-to-bridge loop is still pending until the updated firmware is flashed and tested on hardware.
- If audio init or capture fails during the next on-device interaction test, the firmware falls back to a short silent WAV so the bridge/API/LED loop can still be tested.

## Quick Start Sketch

Bridge:

```bash
./scripts/run_bridge_live.sh
```

The launcher reads the OpenAI API key from macOS Keychain. On first use it
prompts with hidden input and saves the key under service
`wearabllm-openai-api-key`; it never writes the key to the repository or shell
history. Remove the saved key with:

```bash
security delete-generic-password -a "$USER" -s wearabllm-openai-api-key
```

The live launcher also enables conservative automatic durable-memory extraction.
Only extracted stable facts are stored, not audio or full conversations. The
live MEM store is `$HOME/Projects/MEMORY`, scoped as local-only
personal records under source `wearabllm-home-assistant`. If MEM is unavailable,
the bridge falls back to `~/.wearabllm/memory.json`. Inspect or remove shared
records from `v3_WAVESHARE/` with:

```bash
./scripts/memory_admin.py list
./scripts/memory_admin.py forget "green tea"
./scripts/memory_admin.py clear
```

Use `./scripts/memory_admin.py --backend local ...` for the fallback JSON file.

Set `WEARABLLM_DURABLE_MEMORY=0` before running the live launcher to disable the
feature. Session reset does not erase durable memory.

Dry-run bridge helper for first board tests:

```bash
./scripts/run_bridge_dryrun.sh
```

The dry-run helper also enables app-assisted device setup through the local
bridge. It reads the ignored firmware config and uses the port from the staged
bridge URL unless `WEARABLLM_BRIDGE_PORT` is set. After using the app's
`Device Config` panel, rebuild and flash.

In another terminal, verify the bridge endpoints before flashing:

```bash
./scripts/bridge_smoke.sh
```

Without an explicit URL, the smoke helper checks `127.0.0.1` on the port from
the staged firmware bridge URL. Pass a URL or set `WEARABLLM_BRIDGE_BASE_URL`
to check a different bridge.

Check that the bridge, app, firmware, and docs still agree on the 9-command
response scale:

```bash
./scripts/validate_protocol.py
```

Run the full local software preflight:

```bash
./scripts/preflight.sh
```

Compile-check optional firmware paths:

```bash
./scripts/firmware_variant_build.sh display display-test audio-out tts direct-openai transcript-log
```

Print local bench values for firmware `menuconfig` and flashing:

```bash
./scripts/bringup_info.py
```

Patch ignored local firmware config values without opening `menuconfig`:

```bash
export WEARABLLM_WIFI_SSID="your-wifi-name"
export WEARABLLM_WIFI_PASSWORD="your-wifi-password"
export WEARABLLM_WIFI_BSSID="optional-ap-mac"
./scripts/configure_firmware.py
```

For bridge-free use, embed a dedicated OpenAI project key and enable direct
mode. The key is omitted from command history when supplied through the
environment, but it is still recoverable from the flashed firmware. Use a
separate key with a low project budget, not a primary key.

```bash
export WEARABLLM_WIFI_SSID="your-2.4-ghz-wifi-name"
export WEARABLLM_WIFI_PASSWORD="your-wifi-password"
export WEARABLLM_OPENAI_API_KEY="your-dedicated-project-key"
./scripts/configure_firmware.py --enable-direct-openai --clear-bssid
./scripts/firmware_build.sh
./scripts/firmware_flash_monitor.sh
```

After that flash, the board needs only power and internet access. Direct mode
keeps short session context through `previous_response_id`; the bridge's
durable MEM-backed memory is unavailable. Return to local bridge mode with:

```bash
./scripts/configure_firmware.py --disable-direct-openai
```

### Private Supabase transcript log

Create a dedicated Supabase project, then deploy the private table and
token-gated Edge Function using its project reference:

```bash
./scripts/setup_supabase_transcripts.sh your-project-ref
```

The script generates a random device token, stores the matching server secret
in Supabase, and writes the URL/token only to ignored `firmware/sdkconfig`.
It never places the Supabase service/secret key in firmware. The table has RLS
enabled and no client policies; view or delete rows through the Supabase Table
Editor. Supabase may prompt you to log in or enter the new project's database
password while applying the migration.

Complete one USB build/flash after setup:

```bash
./scripts/firmware_build.sh
./scripts/firmware_flash_monitor.sh
```

Uploads run on a background queue. A Supabase outage does not block the device,
although events can be dropped if its four-entry RAM queue fills; this first
version does not persist unsent transcripts across reboot or power loss.

Prepare the next flash for a TFT wiring self-test:

```bash
./scripts/configure_firmware.py --enable-display-self-test
```

Prepare the next flash for an RGB ring command self-test:

```bash
./scripts/configure_firmware.py --enable-led-self-test
```

After the screen wiring is confirmed, disable the boot self-test while keeping
normal display output available:

```bash
./scripts/configure_firmware.py --enable-display --disable-display-self-test
```

Check whether the current ignored firmware config is ready to rebuild/flash:

```bash
./scripts/configure_firmware.py --status
```

To force a no-API LED animation during board testing:

```bash
WEARABLLM_DRY_RUN_COMMAND=RF ./scripts/run_bridge_dryrun.sh
```

To cycle through every ring command on repeated board presses:

```bash
WEARABLLM_DRY_RUN_SEQUENCE=GS,GP,GC,RS,RF,YP,BS,PS,PP ./scripts/run_bridge_dryrun.sh
```

For first hardware audio debugging, add:

```bash
python wearabllm_bridge.py --host 0.0.0.0 --port 8765 --save-wav-dir ./captures
```

Inspect the newest saved capture:

```bash
python3 scripts/inspect_captures.py --latest
```

Summarize staged firmware config, the newest serial log, and saved WAV together:

```bash
./scripts/bench_report.py
```

Check whether the local bench is ready before flashing/testing:

```bash
./scripts/bench_doctor.py
```

Verify that the linked image matches the staged configuration without touching
the board:

```bash
./scripts/verify_firmware_image.py --require-first-flash-profile
```

App bridge smoke test:

```bash
curl -s -X POST \
  -H "Content-Type: application/json" \
  -d '{"transcript":"should I test this path?"}' \
  http://127.0.0.1:8765/v1/query_text
```

Firmware:

```bash
cd firmware
. $HOME/Projects/wearabLLM/.toolchains/esp-idf-v5.5/export.sh
idf.py set-target esp32s3
idf.py menuconfig
idf.py build
idf.py flash monitor
```

Helper equivalents:

```bash
./scripts/firmware_build.sh
./scripts/firmware_flash_monitor.sh
```

In `menuconfig`, set:

- `WearabLLM v3 -> Wi-Fi SSID`
- `WearabLLM v3 -> Wi-Fi password`
- `WearabLLM v3 -> Bridge query URL`, for example `http://192.168.1.23:8765/v1/query`
- `WearabLLM v3 -> Wi-Fi connect wait timeout ms`, default `15000`
- `WearabLLM v3 -> Push-to-talk active level`, default `0`
- `WearabLLM v3 -> Push-to-talk debounce ms`, default `35`
- `WearabLLM v3 -> Push-to-talk GPIO pull mode`, default internal pull-up
- Optional hardware-only check: `WearabLLM v3 -> Run RGB ring command self-test on boot`
- Optional after continuity checks: `WearabLLM v3 -> Enable SPI TFT display`
- Optional TFT wiring check: `WearabLLM v3 -> Run TFT display wiring self-test on boot`
- Optional after mic/bridge stability: `WearabLLM v3 -> Enable ES8311 speaker output`
- Optional speaker volume: `WearabLLM v3 -> Speaker output volume`, default `45`
- Optional after speaker output works: `WearabLLM v3 -> Enable bridge TTS WAV playback`
- Optional TTS buffer limit: `WearabLLM v3 -> Max TTS WAV response bytes`, default `131072`

Android app scaffold:

```bash
cd app
npm install
npm run android
```

Point the app at your bridge base URL, for example `http://192.168.1.23:8765`.

## Documentation

- `FIRST_FLASH.md` - beginner-safe first build, flash, monitor, and hardware test walkthrough
- `docs/ARCHITECTURE.md` - current phase design and phase plan
- `docs/PINMAP.md` - active Waveshare pins and display adapter notes
- `docs/BRINGUP.md` - flashing, bridge testing, and hardware checks
- `docs/BUILD_ENV.md` - local ESP-IDF setup notes
- `docs/STATUS.md` - verified hardware/software status and next bench test
- `protocol/README.md` - HTTP/JSON contract shared by firmware, bridge, and future app
- `app/README.md` - Android-first app scaffold notes
- `scripts/validate_protocol.py` - consistency check for the 9-command response scale
