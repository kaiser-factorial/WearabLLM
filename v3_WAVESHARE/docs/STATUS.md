# v3 Status Log

## Verified On Hardware

Test board:

- Waveshare ESP32-S3-AUDIO-Board
- USB serial/JTAG device detected through the standard `/dev/cu.usbmodem*` path
- ESP32-S3 revision `v0.2`
- 16 MB flash
- 8 MB PSRAM

Verified:

- firmware builds with ESP-IDF v5.5
- firmware flashes over USB serial/JTAG
- bootloader and app boot successfully
- PSRAM memory test passes
- app reaches `WearabLLM v3 Waveshare phase-1 scaffold`
- optional display path stays disabled by default
- ES7210 microphone codec initializes and reports `ES7210 microphone capture ready`
- firmware logs configured bridge URL at boot
- firmware logs connected Wi-Fi AP BSSID/channel/RSSI after association
- firmware now gives a clear project-level warning when Wi-Fi credentials are not configured
- timed serial capture helper saves boot logs from the physical board
- ignored local `firmware/sdkconfig` supports machine-local Wi-Fi and API credentials without publishing them
- firmware rebuild passes with configured Wi-Fi credentials and the current bridge URL

Most recent observed boot state:

```text
wearabllm_display: display disabled; using serial logs only
wearabllm_audio: ES7210 microphone capture ready
wearabllm: Wi-Fi disabled: WearabLLM v3 -> Wi-Fi SSID is empty
wearabllm: Set local credentials with scripts/configure_firmware.py before bridge tests
wearabllm: WearabLLM v3 Waveshare phase-1 scaffold
wearabllm: PTT GPIO=0 LED GPIO=38 bridge=http://<local-bridge-host>:8765/v1/query
wearabllm: Wi-Fi SSID configured=no
```

## Verified Locally

- bridge unit tests pass
- bridge dry-run `/health`, `/v1/query`, `/v1/query_text`, and `/v1/tts` smoke checks pass
- bridge unit suite currently covers 38 cases
- bridge endpoint errors return JSON responses for cleaner app and firmware handling
- bridge `/health` reports `max_audio_bytes`, and the app displays that as the bridge audio cap
- bridge smoke/preflight verifies oversized `/v1/query` audio is rejected with HTTP 413 JSON
- bridge parser accepts two-line, labeled, JSON, fenced JSON, and embedded JSON LLM responses
- bridge validates dry-run command overrides against the shared 9-command response scale
- dry-run `/v1/query` accepts generated non-silent `audio/wav` without STT/API calls
- dry-run command sequences cycle LED commands for repeated board interactions
- Android app protocol test passes
- Android app TypeScript typecheck passes
- Android Expo production export passes (558 modules, 1.63 MB Hermes bundle)
- current default firmware image links successfully; `wearabllm_waveshare.bin` is `0xf4bf0` bytes with 76% of the app partition free
- current image passes the strict pre-flash coherence/profile gate, including embedded bridge URL and source freshness checks
- `scripts/preflight.sh` runs the current local software gate
- `scripts/preflight.sh` includes protocol consistency validation across firmware, bridge, app, protocol docs, and `SPEC.md`
- `scripts/configure_firmware.py --status` reports the ignored local firmware config as ready for the board-to-bridge dry-run test
- firmware parses the bridge `transcript` field for serial logs and the optional TFT heard/reply layout
- `scripts/firmware_variant_build.sh` compile-checks optional paths without changing the ignored local `sdkconfig`
- optional `display`, `display-test`, `audio-out`, and `tts` firmware variants build locally
- speaker output initialization follows the Waveshare demo's MCLK source and TCA9555 `EXIO8` amplifier enable path
- optional TFT boot wiring self-test is available behind `CONFIG_WEARABLLM_DISPLAY_SELF_TEST_ON_BOOT`
- `scripts/serial_capture.sh` can save bounded serial logs under ignored `logs/`
- `scripts/analyze_serial_log.py` summarizes saved serial logs, including Wi-Fi AP details when present, and can fail a run unless the Wi-Fi/capture/bridge/command loop is observed
- `scripts/bench_report.py` combines staged firmware config, the newest serial log, and bridge WAV capture into one loop/audio gate
- `scripts/bench_doctor.py` detects a stale staged bridge IPv4 address and prints the corrective rebuild command
- firmware exposes push-to-talk capture minimum and maximum duration in `menuconfig` and boot logs
- firmware exposes push-to-talk GPIO active level and internal pull mode for BOOT-button or external-button wiring
- microphone capture mixes all four ES7210 TDM lanes and logs per-lane peak/RMS diagnostics before posting the mono WAV
- each interaction logs capture source and total latency so silent fallback cannot be mistaken for physical mic success
- Android app can store and send Wi-Fi, BSSID, PTT GPIO, active level, debounce, pull mode, RGB self-test, speaker output, TTS playback, and TFT bring-up settings through the opt-in bridge device-config endpoint

## Not Yet Verified

- Wi-Fi association on the board with local SSID/password
- firmware image built with Wi-Fi credentials flashed to the board
- board push-to-talk capture after Wi-Fi is configured
- saved bridge WAV from the physical board has audible/non-silent microphone audio
- end-to-end board loop:

```text
BOOT/PTT -> white ring -> onboard mic capture -> bridge /v1/query -> LED command
```

- TFT display wiring and optional `CONFIG_WEARABLLM_DISPLAY_ENABLED` build on the physical display
- ES8311 speaker output on physical speaker hardware
- firmware TTS playback from `/v1/tts`

## Next Hardware Test

1. Confirm local Wi-Fi credentials in ignored `firmware/sdkconfig`:

```bash
cd v3_WAVESHARE
./scripts/configure_firmware.py --status
```

2. Start dry-run bridge with command cycling:

```bash
WEARABLLM_DRY_RUN_SEQUENCE=GS,GP,GC,RS,RF,YP,BS,PS,PP ./scripts/run_bridge_dryrun.sh
```

3. Build, flash, and monitor:

```bash
./scripts/firmware_build.sh
./scripts/firmware_flash_monitor.sh
```

4. Hold BOOT, speak, release, then inspect the saved WAV:

```bash
python3 scripts/inspect_captures.py --latest
```

For a saved boot log:

```bash
./scripts/serial_capture.sh --seconds 20 --reset
./scripts/analyze_serial_log.py
```
