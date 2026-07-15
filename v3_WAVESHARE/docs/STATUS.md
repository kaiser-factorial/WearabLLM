# v3 Status Log

Last updated: 2026-07-10

## Verified On Hardware

Test board:

- Waveshare ESP32-S3-AUDIO-Board
- USB serial/JTAG via `/dev/cu.usbmodem*`
- ESP32-S3 revision `v0.2`
- 16 MB flash
- 8 MB PSRAM

Verified:

- firmware builds with ESP-IDF v5.5 and flashes over USB serial/JTAG
- bootloader and app boot successfully; PSRAM memory test passes
- ES7210 microphone and ES8311 speaker-driver init observed in serial logs
- local `Hi ESP` wake-word model loads on the physical board
- board joins home Wi-Fi without a laptop runtime dependency
- BOOT/PTT and `Hi ESP` complete the onboard mic -> bridge -> LED/speaker loop
- physical mic captures are non-silent and suitable for live transcription
- bridge/hosted TTS plays through the ES8311 speaker
- optional private Supabase transcript logging works with the localhost viewer
- K1/+ and K3/- volume buttons initialize through the TCA9555 expander
- volume preference defaults ship at compile time (current staged default: 70)

## Control Surface (Current Firmware)

| Control | Behavior |
|---|---|
| BOOT / PTT | hold-to-talk capture |
| `Hi ESP` | wake-word capture until silence |
| K1 / + | volume up by 10 (0–100), short earcon, NVS save |
| K2 / set | mute toggle; amber ring while muted; NVS save |
| K3 / - | volume down by 10 (0–100), short earcon, NVS save |

Mute and volume load from NVS at boot after audio init.

## Verified Locally

- bridge unit suite: 61 tests
- protocol consistency across firmware, bridge, app, protocol docs, and `SPEC.md`
- dry-run bridge smoke for `/health`, `/v1/query`, `/v1/query_text`, `/v1/tts`
- Android protocol tests and TypeScript typecheck
- optional firmware variants: display, display-test, audio-out, tts, direct-openai, transcript-log
- hosted-agent Docker Space scaffold + deploy helper present
- Supabase migrations for agent memory, conversation turns, and session archive

## Not Yet Verified / Open

- one complete **hosted** physical spoken loop after the latest flash:

```text
Hi ESP / PTT -> mic -> Hugging Face agent (OpenRouter) -> LED + TTS -> Supabase row
```

- physical confirmation that K1/K2/K3 produce the expected volume/mute changes and that NVS prefs survive reboot
- TFT display wiring and normal display driver output
- authenticated OTA updater and two-slot partition layout
- BLE/SoftAP live provisioning (config still requires rebuild/flash)
- correction-aware durable-memory replacement quality on live benches

## Next Hardware Test

1. Confirm ignored firmware config is ready:

```bash
cd v3_WAVESHARE
./scripts/configure_firmware.py --status
```

2. Build, flash, and capture boot:

```bash
export IDF_PATH="$HOME/Projects/wearabLLM/.toolchains/esp-idf-v5.5"
./scripts/firmware_build.sh
./scripts/serial_capture.sh --seconds 25 --reset
./scripts/analyze_serial_log.py
```

3. Press K1 / K2 / K3 and confirm serial logs:

```text
volume up -> …
mute toggle -> muted
volume prefs loaded: …
```

4. After a reboot, confirm the last volume/mute choice is restored.

5. Optional spoken loop against the hosted agent if the board is configured for that bridge URL.
