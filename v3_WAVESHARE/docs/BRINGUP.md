# v3 Bring-Up Notes

For the current verified board/software state, see `docs/STATUS.md`.

## Hardware Checks Before Power

For the TFT perfboard adapter:

1. Check continuity from Waveshare `3V3` to TFT `VCC`.
2. Check continuity from Waveshare `GND` to TFT `GND`.
3. Confirm there is no continuity between `3V3` and `GND`.
4. Confirm TFT `SCL` goes to `GPIO4`, not Waveshare I2C `SCL`.
5. Confirm TFT `SDA` goes to `GPIO9`, not Waveshare I2C `SDA`.

## Flash/Test Checklist

Use this order so each layer is proven before adding the next one.

Helper scripts exist for the repeated bench commands:

```bash
cd v3_WAVESHARE
./scripts/bringup_info.py
./scripts/configure_firmware.py --dry-run
./scripts/configure_firmware.py --status
./scripts/run_bridge_dryrun.sh
./scripts/bridge_smoke.sh
./scripts/firmware_build.sh
./scripts/firmware_flash_monitor.sh
./scripts/serial_capture.sh --seconds 20 --reset
./scripts/analyze_serial_log.py
```

The detailed commands below are still useful when you need to inspect or change one step.

### 1. Start With Display And Speaker Disabled

For the first flash, leave these off in `idf.py menuconfig`:

```text
WearabLLM v3 -> Enable SPI TFT display -> off
WearabLLM v3 -> Enable ES8311 speaker output -> off
WearabLLM v3 -> Enable bridge TTS WAV playback -> off
```

This keeps the first test focused on:

```text
button -> onboard mic -> bridge -> LED ring
```

If you want to test only the RGB ring before Wi-Fi/mic/bridge, temporarily enable:

```text
WearabLLM v3 -> Run RGB ring command self-test on boot -> on
```

On boot, the firmware runs all 9 command animations once and returns to idle blue. Disable it again before normal push-to-talk tests so boot is quiet and predictable.

### 2. Start The Bridge In Dry-Run Mode

From the repo root:

```bash
cd v3_WAVESHARE/bridge
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python wearabllm_bridge.py --host 0.0.0.0 --port 8765 --dry-run --save-wav-dir ./captures
```

Or use the helper:

```bash
cd v3_WAVESHARE
./scripts/run_bridge_dryrun.sh
```

To force a specific dry-run LED command for ring animation testing:

```bash
WEARABLLM_DRY_RUN_COMMAND=GC ./scripts/run_bridge_dryrun.sh
```

To cycle through all LED commands on repeated board interactions:

```bash
WEARABLLM_DRY_RUN_SEQUENCE=GS,GP,GC,RS,RF,YP,BS,PS,PP ./scripts/run_bridge_dryrun.sh
```

Useful no-API animation checks:

| Command | Expected ring check |
|---|---|
| `GP` | green pulse |
| `GC` | green chase |
| `RF` | red flicker |
| `YP` | yellow pulse |
| `PP` | purple pulse |

In a second terminal, verify all dry-run bridge endpoints:

```bash
cd v3_WAVESHARE
./scripts/bridge_smoke.sh
```

In dry-run mode, this also posts a generated non-silent `audio/wav` to `/v1/query`, which is the same endpoint used by the firmware. In live mode it skips that audio query by default to avoid unplanned STT/API calls. To force the audio query anyway:

```bash
WEARABLLM_SMOKE_AUDIO=1 ./scripts/bridge_smoke.sh
```

Keep this terminal open. Find your computer's LAN IP from another terminal:

```bash
cd v3_WAVESHARE
./scripts/bringup_info.py
```

That helper also prints the bridge URLs to copy into firmware `menuconfig` and any detected ESP32 serial ports.

You can also query macOS directly:

```bash
ipconfig getifaddr en0
```

If that returns nothing because you are on a different network adapter, use:

```bash
ifconfig | grep 'inet '
```

Confirm the bridge mode before flashing:

```bash
curl -s http://127.0.0.1:8765/health
```

Expected dry-run health includes:

```json
{"ok":true,"service":"wearabllm-bridge","config":{"dry_run":true,"dry_run_command":"BS","stt":"openai","llm_model":"gpt-5.4-mini"}}
```

Bridge health also reports `max_audio_bytes`. The default is `524288`, which is
above the current firmware's 6-second 16 kHz mono WAV cap. If you intentionally
raise firmware capture duration, raise the bridge limit too:

```bash
WEARABLLM_MAX_AUDIO_BYTES=1048576 ./scripts/run_bridge_dryrun.sh
```

### 3. Configure Firmware

In another terminal:

```bash
cd v3_WAVESHARE/firmware
. /Users/corinakaiser/Projects/wearabLLM/.toolchains/esp-idf-v5.5/export.sh
idf.py set-target esp32s3
idf.py menuconfig
```

Set:

```text
WearabLLM v3 -> Wi-Fi SSID
WearabLLM v3 -> Wi-Fi password
WearabLLM v3 -> Bridge query URL -> http://<computer-lan-ip>:8765/v1/query
WearabLLM v3 -> Wi-Fi connect wait timeout ms -> 15000
WearabLLM v3 -> Push-to-talk GPIO -> 0
WearabLLM v3 -> Push-to-talk active level -> 0
WearabLLM v3 -> Push-to-talk GPIO pull mode -> Internal pull-up
WearabLLM v3 -> Minimum push-to-talk capture ms -> 250
WearabLLM v3 -> Maximum push-to-talk capture seconds -> 6
```

The default `GPIO0` uses the BOOT button as the first push-to-talk button. That
path is active-low with the internal pull-up enabled.

For a simple external pushbutton:

- wire GPIO to `GND` when pressed: use active level `0` and pull `up`
- wire GPIO to `3V3` when pressed: use active level `1` and pull `down`
- use pull `none` only when the perfboard circuit already has its own bias resistor

To print the exact bridge query/TTS URLs before opening `menuconfig`:

```bash
cd v3_WAVESHARE
./scripts/bringup_info.py
```

If you want to avoid `menuconfig` for local bench values, use the local
configuration helper. This writes only ignored `firmware/sdkconfig` values and
keeps your Wi-Fi password out of git:

```bash
cd v3_WAVESHARE
export WEARABLLM_WIFI_SSID="your-wifi-name"
export WEARABLLM_WIFI_PASSWORD="your-wifi-password"
./scripts/configure_firmware.py
```

If your network has multiple access points with the same name, you can
optionally pin the ESP32 to one AP MAC/BSSID:

```bash
export WEARABLLM_WIFI_BSSID="ca:50:35:23:2b:1f"
./scripts/configure_firmware.py
```

Leave BSSID blank for normal SSID roaming. A BSSID is not a bridge IP address;
the bridge URL still needs this computer's LAN IP.

To preview without writing:

```bash
./scripts/configure_firmware.py --dry-run
```

To check the current ignored `firmware/sdkconfig` before rebuilding:

```bash
./scripts/configure_firmware.py --status
```

For bridge/app tooling that needs the same information without parsing text:

```bash
./scripts/configure_firmware.py --status-json
```

`--status` prints the configured bridge URL, PTT GPIO, PTT active level, PTT
debounce, PTT pull mode, Wi-Fi timeout, capture timing, TFT display enablement,
and TFT boot self-test state, plus whether both Wi-Fi fields are set. It exits
with status `1` when the board is not ready for a board-to-bridge test, which
is expected while credentials are still blank and useful for scripts.

To change the push-to-talk wiring without opening `menuconfig`:

```bash
./scripts/configure_firmware.py --ptt-gpio 0 --ptt-active-level 0 --ptt-debounce-ms 35 --ptt-pull up
```

To tune push-to-talk capture limits without opening `menuconfig`:

```bash
./scripts/configure_firmware.py --audio-min-capture-ms 250 --audio-max-seconds 6
```

To prepare the next flash for the TFT wiring self-test:

```bash
./scripts/configure_firmware.py --enable-display-self-test
```

To prepare the next flash for an RGB ring command self-test:

```bash
./scripts/configure_firmware.py --enable-led-self-test
```

To leave normal TFT output enabled after the wiring test, but stop running the
boot self-test every reset:

```bash
./scripts/configure_firmware.py --enable-display --disable-display-self-test
```

The helper auto-detects this computer's LAN IP for:

```text
WearabLLM v3 -> Bridge query URL -> http://<computer-lan-ip>:8765/v1/query
WearabLLM v3 -> Bridge TTS URL   -> http://<computer-lan-ip>:8765/v1/tts
```

You can override detection:

```bash
./scripts/configure_firmware.py --bridge-host 192.168.86.31
```

### 4. Build, Flash, And Monitor

With the board connected over USB:

```bash
idf.py build
idf.py -p /dev/tty.usbmodem* flash monitor
```

Or use:

```bash
cd v3_WAVESHARE
./scripts/firmware_build.sh
./scripts/firmware_flash_monitor.sh
```

If the wildcard does not match, list serial devices:

```bash
ls /dev/tty.usb*
ls /dev/cu.usb*
```

Then retry with the exact port, for example:

```bash
idf.py -p /dev/tty.usbmodem1101 flash monitor
```

The flash helper also accepts an explicit port:

```bash
./scripts/firmware_flash_monitor.sh /dev/tty.usbmodem1101
```

To exit the serial monitor:

```text
Ctrl+]
```

To save a bounded boot log instead of opening an interactive monitor:

```bash
cd v3_WAVESHARE
./scripts/serial_capture.sh --seconds 20 --reset /dev/cu.usbmodem101
```

Logs are written under ignored `logs/` by default.

Before flashing or starting a board interaction, run the bench doctor. It reads
the ignored firmware config, tries the configured bridge `/health`, and
summarizes the latest serial/WAV evidence without touching the board:

```bash
./scripts/bench_doctor.py
```

To summarize the newest saved log:

```bash
./scripts/analyze_serial_log.py
```

For a pass/fail check of the full first loop:

```bash
./scripts/analyze_serial_log.py --require-loop
```

After a bridge capture exists, summarize the latest serial log and latest WAV
together:

```bash
./scripts/bench_report.py
```

For a stricter hardware gate after one full interaction:

```bash
./scripts/bench_report.py --require-loop --require-audible
```

### 5. Expected First Boot Logs

Look for:

```text
WearabLLM v3 Waveshare phase-1 scaffold
Wi-Fi connected: <ip address>
Wi-Fi AP: ssid=<network> bssid=<ap-mac> channel=<channel> rssi=<dbm> auth=<mode>
ES7210 microphone capture ready
PTT GPIO=0 active_level=0 pull=pull-up LED GPIO=38 bridge=http://<computer-lan-ip>:8765/v1/query
Audio capture min=250 ms max=6 s
Wi-Fi SSID configured=yes
```

If credentials are not configured yet, expected logs are:

```text
Wi-Fi disabled: WearabLLM v3 -> Wi-Fi SSID is empty
Set local credentials with scripts/configure_firmware.py before bridge tests
Wi-Fi SSID configured=no
```

If Wi-Fi does not connect, the station keeps retrying. During an interaction, firmware waits up to `Wi-Fi connect wait timeout ms`, then shows/logs an error instead of staying at `THINKING` forever.

If a BSSID/AP MAC is pinned, confirm the logged `bssid` matches the intended
access point. `rssi` is signal strength in dBm; values closer to zero are
stronger.

The serial analyzer looks for these same boot and interaction signals and
prints the most likely next check if the loop is incomplete:

```bash
cd v3_WAVESHARE
./scripts/analyze_serial_log.py logs/serial-YYYYmmdd-HHMMSS.log
```

### 6. First Interaction Test

Hold the BOOT button, say a short phrase, then release.

Expected board behavior:

```text
hold button -> LED ring white
release button -> LED ring amber/thinking
bridge response -> LED ring blue for dry-run BS
```

Expected serial monitor details include:

```text
push-to-talk held: listening
capture stats: duration=<ms> samples=<count> peak=<level> rms=<level> appears_silent=<yes/no>
captured <n> PCM bytes, WAV bytes=<n>
posting WAV to bridge: <n> bytes -> http://<computer-lan-ip>:8765/v1/query
bridge HTTP result: err=ESP_OK status=200 response_bytes=<n>
transcript: <recognized speech>
bridge command=BS reply_len=<n>
LED command: BS
```

If the bridge returns malformed JSON, an unknown LED code, or a response larger
than the firmware's current response buffer, the board logs a bridge request
failure and shows the error color/display state instead of silently treating it
as blue.

If `appears_silent=yes`, the board still posted a valid WAV, but the capture probably has no useful microphone audio. Check the saved bridge capture before moving to live STT.

Expected bridge terminal output:

```text
Transcript: <dry-run typed transcript or empty audio>
Command   : BS, or your forced WEARABLLM_DRY_RUN_COMMAND
Reply     : Dry run transcript: ...
Saved WAV : captures/wearabllm-...
```

Open the saved WAV:

```bash
afinfo captures/wearabllm-*.wav
open captures/wearabllm-*.wav
```

Or inspect the bridge's saved WAV metadata and audio levels from the repo:

```bash
cd v3_WAVESHARE
python3 scripts/inspect_captures.py --latest
```

For a pass/fail audio check:

```bash
python3 scripts/inspect_captures.py --latest --require-audible
```

Useful first-audio signals:

- `appears_silent=no` means the mic path is probably producing non-empty audio.
- `appears_silent=yes` means either the room was quiet, the mic path failed, or the firmware fell back to its silent WAV.
- `duration_ms` should roughly match how long you held the push-to-talk button, up to the firmware capture limit.
- Very short taps still capture the configured minimum duration so the bridge receives a valid WAV during debounce/handling tests.

Do not move to live OpenAI/STT until this saved WAV has real audible mic audio.

### 7. Live STT/LLM Test

After the dry-run audio file sounds usable, restart the bridge without `--dry-run`:

```bash
cd v3_WAVESHARE/bridge
source .venv/bin/activate
export OPENAI_API_KEY="..."
python wearabllm_bridge.py --host 0.0.0.0 --port 8765 --save-wav-dir ./captures
```

Flash does not need to change if the bridge URL is the same. Hold BOOT, ask a simple yes/no question, and confirm:

- bridge prints a real transcript
- bridge returns one of `GS`, `GP`, `GC`, `RS`, `RF`, `YP`, `BS`, `PS`, `PP`
- LED ring changes to the matching valence color

### 8. Enable TFT Display

Only after the LED/mic/bridge loop works, enable:

```text
WearabLLM v3 -> Enable SPI TFT display -> on
```

For a display-only wiring check, also temporarily enable:

```text
WearabLLM v3 -> Run TFT display wiring self-test on boot -> on
```

Confirm the configured pins still match the perfboard:

```text
SCLK GPIO4
MOSI GPIO9
CS   GPIO3
DC   GPIO7
RST  GPIO6
BL   GPIO5, or ignored if BLK is tied to 3V3
```

Then:

```bash
idf.py build
idf.py -p /dev/tty.usbmodem* flash monitor
```

Expected display states:

- boot self-test: red, green, blue, yellow, purple, white bands, then readable pin-map text
- `READY`
- `LISTENING`
- `THINKING`
- response code, heard transcript, and reply text
- error message if bridge/Wi-Fi/audio fails

### 9. Enable Speaker Earcon

Only after mic/bridge/display are stable, enable:

```text
WearabLLM v3 -> Enable ES8311 speaker output -> on
WearabLLM v3 -> Speaker output volume -> 45
```

Then rebuild/flash. After a successful bridge response, expect a short tone. If the tone fails, the LED/display path should still work.

### 10. Enable TTS Playback

Only after the speaker tone works, enable:

```text
WearabLLM v3 -> Enable bridge TTS WAV playback -> on
WearabLLM v3 -> Bridge TTS URL -> http://<computer-lan-ip>:8765/v1/tts
WearabLLM v3 -> Max TTS WAV response bytes -> 131072
```

Start with the bridge in `--dry-run` mode. Dry-run TTS returns a valid silent WAV, so this only proves the HTTP/WAV/playback path. Then try live TTS after the silent path succeeds.

## Bridge Smoke Test

Start the bridge without API calls:

```bash
cd v3_WAVESHARE/bridge
python3 wearabllm_bridge.py --dry-run --typed "is this working?"
```

In another terminal:

```bash
printf "not really audio" > /tmp/fake.wav
curl -s -X POST \
  -H "Content-Type: audio/wav" \
  --data-binary @/tmp/fake.wav \
  http://127.0.0.1:8765/v1/query
```

Expected shape:

```json
{"command":"BS","reply":"Dry run transcript: is this working?","transcript":"is this working?","audio_bytes":16,"saved_wav":null}
```

For app/manual testing without WAV upload:

```bash
curl -s -X POST \
  -H "Content-Type: application/json" \
  -d '{"transcript":"is the text endpoint working?"}' \
  http://127.0.0.1:8765/v1/query_text
```

Expected dry-run shape:

```json
{"command":"BS","reply":"Dry run transcript: is the text endpoint working?","transcript":"is the text endpoint working?","audio_bytes":0,"saved_wav":null}
```

For phase-2 TTS scaffolding without API calls, run the bridge with `--dry-run` and request a WAV:

```bash
curl -s -X POST \
  -H "Content-Type: application/json" \
  -d '{"text":"yes, this is the TTS path"}' \
  http://127.0.0.1:8765/v1/tts \
  -o /tmp/wearabllm-tts.wav
afinfo /tmp/wearabllm-tts.wav
```

Dry-run TTS returns a valid silent `16 kHz`, mono, 16-bit WAV. Live TTS requires `OPENAI_API_KEY` and uses:

```text
--tts-model gpt-4o-mini-tts
--tts-voice alloy
```

## OpenAI Bridge Test

```bash
cd v3_WAVESHARE/bridge
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY="..."
python wearabllm_bridge.py
```

The bridge listens on:

```text
http://0.0.0.0:8765/v1/query
```

Check the active model/backend settings:

```bash
curl -s http://127.0.0.1:8765/health
```

Use your computer's LAN IP in the ESP32 firmware config, for example:

```text
http://192.168.1.23:8765/v1/query
```

## Save Received Audio

For the first real board test, run the bridge with WAV saving enabled:

```bash
python wearabllm_bridge.py --save-wav-dir ./captures --dry-run
```

Then hold the device button and speak a short fixed phrase, such as:

```text
testing one two three
```

The bridge response includes:

```json
{
  "audio_bytes": 123456,
  "saved_wav": "captures/wearabllm-20260612-120000-001.wav",
  "wav_info": {
    "valid": true,
    "sample_rate": 16000,
    "channels": 1,
    "sample_width_bytes": 2,
    "duration_ms": 1800,
    "peak_abs": 1200,
    "rms_dbfs": -36.7,
    "appears_silent": false
  }
}
```

Expected first-pass `wav_info`:

- `valid`: `true`
- `sample_rate`: `16000`
- `channels`: `1`
- `sample_width_bytes`: `2`
- `duration_ms`: roughly how long the button was held, capped by firmware's max capture length
- `peak_abs`: nonzero for real captured signal
- `rms_dbfs`: usually a negative number; closer to `0` is louder
- `appears_silent`: should be `false` when you speak into the board

Open or inspect the saved file before debugging the LLM path. This separates three failure classes:

- no/very small `audio_bytes`: firmware did not capture or upload useful audio
- `appears_silent` is `true` or saved WAV is silent/noisy: ES7210/I2S channel, gain, or downmix needs work
- saved WAV sounds good but transcript is wrong: STT configuration/model issue

On macOS, quick local checks:

```bash
afinfo captures/wearabllm-*.wav
open captures/wearabllm-*.wav
```

## Firmware Build

Prerequisite: ESP-IDF installed and exported in the current shell. On this Mac, the local ESP-IDF checkout is:

```text
/Users/corinakaiser/Projects/wearabLLM/.toolchains/esp-idf-v5.5
```

```bash
cd v3_WAVESHARE/firmware
PATH="/Users/corinakaiser/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin:$PATH" \
  bash -c '. /Users/corinakaiser/Projects/wearabLLM/.toolchains/esp-idf-v5.5/export.sh && idf.py build'
```

For normal interactive work:

```bash
cd v3_WAVESHARE/firmware
. /Users/corinakaiser/Projects/wearabLLM/.toolchains/esp-idf-v5.5/export.sh
idf.py set-target esp32s3
idf.py menuconfig
idf.py build
idf.py flash monitor
```

Set the WearabLLM menuconfig values before flashing:

- Wi-Fi SSID
- Wi-Fi password
- bridge URL
- PTT GPIO if not using the default BOOT button

## Optional TFT Display

The ST7735 SPI TFT driver is compiled only when this is enabled:

```text
WearabLLM v3 -> Enable SPI TFT display
```

For a wiring-only boot test, temporarily enable:

```text
WearabLLM v3 -> Run TFT display wiring self-test on boot
```

Leave it disabled until the perfboard adapter passes continuity checks. When enabled, the display uses:

| Signal | GPIO |
|---|---:|
| SCLK | `GPIO4` |
| MOSI | `GPIO9` |
| CS | `GPIO3` |
| DC | `GPIO7` |
| RST | `GPIO6` |
| BL | `GPIO5` |

If your backlight is tied directly to `3V3`, the `BL` GPIO setting is not used electrically. The driver currently assumes a `128x160` ST7735 and exposes `X offset`, `Y offset`, and `Invert TFT colors` in menuconfig for the common breakout variants.

Expected display behavior:

- optional boot self-test: color bands followed by `TFT SELF TEST` and the configured GPIOs
- boot/idle: `READY` and `HOLD BUTTON TO ASK`
- while holding PTT: `LISTENING`
- after release while waiting for bridge/API: `THINKING`
- after bridge response: response code plus wrapped answer text
- on error: short error message

## Optional Speaker Earcon

The ES8311 speaker-output path is compiled only when this is enabled:

```text
WearabLLM v3 -> Enable ES8311 speaker output
```

Leave it disabled until the mic capture and bridge loop are stable. When enabled, firmware initializes the ES8311 DAC over the same board I2C/I2S codec path and plays a short tone after a successful bridge response. Start with a conservative volume:

```text
WearabLLM v3 -> Speaker output volume -> 45
```

Expected speaker behavior:

- boot log includes `ES8311 speaker output ready`
- after a successful bridge reply, a short tone plays
- if speaker init/write fails, the LED/display response should still continue and the firmware logs the speaker error

This is the first speaker bring-up test. Keep it passing before enabling TTS WAV playback.

## Optional Bridge TTS Playback

The bridge can generate a WAV for reply text at `/v1/tts`, and firmware can optionally fetch and play that WAV after a normal `/v1/query` response.

Enable this only after the speaker earcon path works:

```text
WearabLLM v3 -> Enable ES8311 speaker output
WearabLLM v3 -> Enable bridge TTS WAV playback
WearabLLM v3 -> Bridge TTS URL -> http://192.168.1.23:8765/v1/tts
WearabLLM v3 -> Max TTS WAV response bytes -> 131072
```

For a no-API hardware smoke test, run the bridge in dry-run mode:

```bash
cd v3_WAVESHARE/bridge
python wearabllm_bridge.py --host 0.0.0.0 --port 8765 --dry-run
```

Expected TTS playback behavior:

- after a successful bridge reply, the short earcon still plays first
- firmware posts the reply text to `CONFIG_WEARABLLM_TTS_URL`
- the dry-run bridge returns a valid silent 16 kHz mono WAV
- live bridge mode returns speech audio from the configured TTS provider
- if TTS fetch or WAV playback fails, the LED/display response should still continue and the firmware logs the TTS error

The firmware WAV player currently expects PCM, mono, 16-bit WAV. The dry-run bridge path matches this exactly; live provider output should be verified before relying on it for demos.

## Current Expected Firmware Behavior

1. Hold the PTT button.
2. RGB ring turns white.
3. Release the button.
4. Firmware sends captured ES7210/I2S audio as `audio/wav` to the bridge.
5. Bridge returns a response.
6. RGB ring runs the returned command animation, then settles on the command color.

Response animations preserve the original WearabLLM visual grammar:

| Code | Ring behavior |
|---|---|
| `GS` | green solid |
| `GP` | green pulse |
| `GC` | green chase |
| `RS` | red solid |
| `RF` | red flicker |
| `YP` | yellow pulse |
| `BS` | blue solid |
| `PS` | purple solid |
| `PP` | purple pulse |

If ES7210 init or reading fails, firmware logs the error and sends a short silent WAV fallback. That fallback is only there so the network -> bridge -> LLM -> LED path can still be tested while debugging the audio codec.

## Audio Verification

The first real hardware test should answer these questions:

1. Does `wearabllm_audio_init()` log `ES7210 microphone capture ready`?
2. Does holding PTT log a nonzero captured PCM byte count?
3. Does the bridge receive a WAV that transcription can understand?
4. If transcription is garbled, does changing channel selection or gain improve it?

The current downmix path reads 32-bit stereo I2S samples and converts them to 16-bit mono WAV for STT.

## Local Validation

Current local checks:

```bash
python3 -m unittest discover -s v3_WAVESHARE/bridge -p 'test_*.py'
python3 -m py_compile v3_WAVESHARE/bridge/wearabllm_bridge.py v3_WAVESHARE/bridge/test_wearabllm_bridge.py

cd v3_WAVESHARE/firmware
PATH="/Users/corinakaiser/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin:$PATH" \
  bash -c '. /Users/corinakaiser/Projects/wearabLLM/.toolchains/esp-idf-v5.5/export.sh && idf.py build'
```

The firmware build output is `build/wearabllm_waveshare.bin`.

To compile-check optional firmware paths without changing the repo's normal
ignored `firmware/sdkconfig`, use the variant helper:

```bash
cd v3_WAVESHARE
./scripts/firmware_variant_build.sh display
./scripts/firmware_variant_build.sh display-test
./scripts/firmware_variant_build.sh audio-out
./scripts/firmware_variant_build.sh tts
```

To build the normal firmware plus all optional variants:

```bash
./scripts/firmware_variant_build.sh all
```

Available variants:

- `default`
- `led-self-test`
- `display`
- `display-test`
- `audio-out`
- `tts`
- `all`

## Useful Audio References

Useful Waveshare demo references:

- `ESP32-S3-AUDIO-Board-Demo/ESP-IDF/esp_sr_02/main/hardeware_driver/bsp_board.h`
- `ESP32-S3-AUDIO-Board-Demo/ESP-IDF/esp_sr_02/main/hardeware_driver/bsp_board.c`
- `ESP32-S3-AUDIO-Board-Demo/ESP-IDF/factory_01/main/speech_det_driver/`

The relevant audio pins are documented in `PINMAP.md`.
