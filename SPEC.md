# WearabLLM Project Spec

Last updated: 2026-06-23

## One-Line Summary

WearabLLM is a physical LLM interface for talking to an assistant and receiving an embodied response through light, sound, and a small optional display.

## Project Intent

The core idea is:

```text
user speaks -> bridge/app transcribes -> LLM interprets -> device responds physically
```

The device is meant to feel like a small expressive companion or oracle rather than a general-purpose chatbot screen. The LLM's text answer can still appear in an app, but the primary output language is physical: LED color, LED motion, sound, and potentially haptic feedback.

The project began on an Adafruit Circuit Playground Bluefruit because that board provided many useful capabilities in one package: BLE, NeoPixels, a speaker, buttons, slide switch, temperature/light/accelerometer sensors, and battery-friendly operation. A servo reveal mechanism was later added as a cute physical actuation experiment. The current direction is to remove or de-emphasize the servo and focus on stronger voice capture, LED expression, sound, and a compact display.

## Current User Goals

- Build the v3 prototype on the Waveshare ESP32-S3-AUDIO-Board.
- Use the board's onboard microphone path so users no longer need to speak into the phone for the first hardware loop.
- Use the board's 7-pixel RGB ring as the first valence display.
- Support both BOOT-button push-to-talk and hands-free activation with the on-device `Hi ESP` wake word.
- Keep recent conversation turns in bridge memory so follow-up questions have context.
- Use the onboard speaker for bridge-generated OpenAI TTS, with physical volume controls.
- Keep the optional SPI TFT gated off until its perfboard wiring is verified.
- Preserve the existing semantic response scale, but represent it more richly:
  - color communicates response category or tone
  - number of lit LEDs communicates confidence or certainty
  - animation communicates energy, urgency, or nuance
- 86 the servo direction unless there is a strong reason to keep it.

## What Exists Today

### Main Project Trees

There are active and historical project folders:

- `v3_WAVESHARE/`: active Waveshare ESP32-S3-AUDIO-Board firmware, local bridge, Android app scaffold, protocol docs, and bring-up scripts.
- `WearabLLM/`: older intended main app/firmware tree in some snapshots.
- `servo_bluefruit/WearabLLM_Codex/`: contains a fuller older snapshot with readable docs and firmware.
- `servo_bluefruit/WearabLLM/desktop-app/`: contains an Electron desktop scaffold for BLE and LLM command testing.
- `servo_bluefruit/servo_potentiometer_copy/`: older Arduino servo experiment.

Important note for future agents: some files in `WearabLLM/` currently appear as local placeholder files on macOS/iCloud. They have file sizes but zero allocated blocks, and reads may time out. If a file in `WearabLLM/` cannot be read, check the matching file under `servo_bluefruit/WearabLLM_Codex/` or the desktop scaffold.

### v3 Waveshare Prototype

Path: `v3_WAVESHARE/`

Target board:

- Waveshare ESP32-S3-AUDIO-Board
- ESP32-S3, 16 MB flash observed, 8 MB PSRAM observed
- onboard ES7210 microphone codec path
- onboard ES8311 speaker codec path
- 7 onboard WS2812 RGB LEDs on the board ring
- optional SPI TFT on a perfboard adapter, currently mapped as SPI rather than I2C

Current interaction loop:

```text
say "Hi ESP" or hold BOOT/PTT
-> dim-white RGB ring while listening
-> board captures WAV from onboard mic
-> board posts audio/wav to local bridge over Wi-Fi
-> bridge performs STT and context-aware LLM response selection
-> bridge returns command + reply JSON
-> board applies LED valence command and plays the reply through TTS
```

Current v3 implementation:

- ESP-IDF firmware in `v3_WAVESHARE/firmware`
- local Python bridge in `v3_WAVESHARE/bridge`
- Android-first Expo scaffold in `v3_WAVESHARE/app`
- HTTP protocol docs in `v3_WAVESHARE/protocol`
- hardware bring-up docs in `v3_WAVESHARE/docs`
- bench helpers in `v3_WAVESHARE/scripts`

Current v3 firmware capabilities:

- BOOT-button hold-to-talk state machine
- on-device ESP-SR WakeNet9 wake-word detection for `Hi ESP`; BOOT PTT remains available
- configurable PTT GPIO, active level, and internal pull mode for BOOT-button testing or later external-button wiring
- LED states: dim blue idle, dim neutral white listening, bright neutral white thinking, red error, response command color/animation
- Wi-Fi station setup with local ignored `sdkconfig` credentials and optional BSSID/AP MAC pinning
- serial Wi-Fi diagnostics that include connected AP BSSID, channel, RSSI, and auth mode after association
- HTTP POST to the bridge
- ES7210/I2S microphone WAV capture
- silent WAV fallback if audio initialization or capture fails, so the network/bridge/LED path can still be tested
- configurable minimum and maximum capture duration; current staged maximum is 15 seconds
- wake-word capture ends after 1.4 seconds of detected silence, up to the configured maximum
- serial capture diagnostics: duration, peak, RMS, silence flag
- strict bridge command validation for the 9-command response scale
- optional ST7735 TFT status/response display behind `CONFIG_WEARABLLM_DISPLAY_ENABLED`
- optional TFT boot wiring self-test behind `CONFIG_WEARABLLM_DISPLAY_SELF_TEST_ON_BOOT`
- ES8311 speaker earcon and WAV playback behind `CONFIG_WEARABLLM_AUDIO_OUT_ENABLED`
- bridge TTS WAV fetch/playback behind `CONFIG_WEARABLLM_TTS_ENABLED`
- K1/+ and K3/- physical volume controls in 10-point increments over 0-100; current boot default is 70 and K2/set remains reserved

Current v3 bridge capabilities:

- `GET /health`
- `POST /v1/query` with `audio/wav`
- `POST /v1/query_text` for typed/app testing
- `POST /v1/tts` for phase-2 WAV output
- `POST /v1/session/reset` to clear in-process conversation history
- `POST /v1/device_wifi` for opt-in app-assisted firmware config updates
- OpenAI STT path using `gpt-4o-transcribe`
- in-session LLM context, retaining 20 user/assistant turns by default and configurable with `WEARABLLM_HISTORY_TURNS`
- shared cross-session memory, enabled by the live launcher, that auto-extracts conservative stable user facts into `$HOME/Projects/MEMORY`
- MEM records scoped to source `wearabllm-home-assistant`, personal assistant tags, and `localOnly` cloud-sync exclusion
- bounded semantic retrieval (three records by default), duplicate suppression, list/forget/clear administration, and private JSON fallback when MEM is unavailable
- optional local Whisper path
- dry-run mode with fixed or sequenced LED commands
- saved WAV capture inspection for first hardware audio validation
- configurable upload-size cap for `/v1/query`, reported by `/health` and enforced with JSON 413 errors
- tolerant live LLM response parser that normalizes plain two-line output, labeled text, fenced JSON, embedded JSON, and small JSON into the shared protocol shape
- OpenAI `gpt-4o-mini-tts` output using the `verse` voice and the configured theatrical delivery instructions; output is normalized to 16 kHz mono 16-bit WAV for the board
- OpenAI API key loading from the `wearabllm-openai-api-key` macOS Keychain service; an environment-provided key is persisted there by `run_bridge_live.sh`
- opt-in local firmware config writer for Wi-Fi, bridge URL, BSSID/AP MAC, PTT GPIO, PTT active level, and PTT pull mode

Current v3 app capabilities:

- Expo Android-first bridge console
- bridge URL storage and `/health` check
- typed transcript test through `/v1/query_text`
- Android native hold-to-speak STT routed to `/v1/query_text`
- command/reply history display
- device Wi-Fi credential form stored in SecureStore
- optional AP MAC/BSSID validation and bridge submission for the next firmware flash
- PTT GPIO, active-level, debounce, and pull-mode controls routed through the opt-in bridge device-config endpoint
- RGB, speaker, TTS, and TFT bring-up toggles routed through the same next-flash device-config path

Verified v3 state:

- firmware builds locally with ESP-IDF v5.5
- firmware has flashed and booted on the physical Waveshare board
- PSRAM and app boot have been observed in serial logs
- ES7210 microphone codec initialization has been observed in serial logs
- board Wi-Fi association and bridge connectivity have been observed on hardware
- BOOT/PTT capture and `Hi ESP` wake-word activation both complete the onboard mic -> bridge -> LED/speaker loop
- captured physical-board microphone audio is non-silent and suitable for live transcription
- ES8311 speaker tones and bridge TTS playback work on the physical speaker
- live `verse` TTS returns a valid normalized WAV and plays through the board
- session-context recall has been exercised through the live bridge
- bridge unit suite passes with 50 tests; bridge smoke tests, Android protocol tests, app typecheck, protocol consistency checks, and firmware builds also pass locally
- optional firmware variant builds pass locally for display, display boot self-test, audio-out, and TTS paths
- app-assisted next-flash config covers Wi-Fi, BSSID, PTT, RGB self-test, speaker output, TTS playback, and TFT toggles
- current firmware image has been built, coherence-verified, flashed, and observed booting with `Hi ESP`, 15-second capture, TTS, and K1/K3 button initialization

Not yet verified or integrated:

- physical TFT wiring, TFT boot self-test output, and normal display driver output
- authenticated ingestion from a separate Home Assistant host
- correction-aware replacement of stale facts and live bench validation of automatic extraction quality
- physical confirmation that K1 and K3 produce the expected 10-point volume changes, although expander initialization, firmware build, flash, and boot are verified
- BLE/SoftAP live provisioning; current device configuration still requires rebuilding and flashing firmware

### v1 Phone App

Path: `WearabLLM/phone-app/`

Stack:

- React Native / Expo
- `react-native-ble-plx` for BLE
- `expo-speech-recognition` for phone-native speech recognition
- `expo-secure-store` for API keys/provider config

Historical/current v1 responsibilities:

- scan for the wearable over BLE
- subscribe to device-originated messages such as button-triggered voice start/stop
- use phone mic and native speech recognition for transcription
- send transcript plus optional sensor context to an LLM
- parse the LLM response into a compact wearable command
- send the command back to the wearable over BLE
- show the LLM's short text answer in the UI

The app has a known iOS 26 workaround: speech recognition event listeners were replaced by polling via `pollEvents()` because the JSI event path was unstable. For v3, Android is the preferred mobile target.

### LLM Layer And Response Contract

Historical v1 path: `WearabLLM/phone-app/src/api/llm.ts`

Current v3 bridge path: `v3_WAVESHARE/bridge/wearabllm_bridge.py`

The v1 phone app has a provider abstraction for:

- Anthropic
- OpenAI-compatible chat completions
- Ollama local models

The LLM prompt asks the model to return:

```text
Line 1: a 2-character LED command
Line 2+: a short conversational answer
```

The v1 parser falls back to `BS` if the command is missing or invalid. The v3 bridge parser is more tolerant of live model formatting, but the v3 firmware rejects unknown command codes so bridge/app/firmware drift is visible during bench tests.

Current 9-code response scale:

| Code | Meaning | Current visual intent |
| --- | --- | --- |
| `GS` | confident yes / affirmation | green solid |
| `GP` | gentle yes / warm agreement | green pulse |
| `GC` | enthusiastic yes | green chase |
| `RS` | firm no / negative | red solid |
| `RF` | warning / urgent concern | red flicker |
| `YP` | uncertain / maybe / nuanced | yellow pulse |
| `BS` | neutral information / acknowledgement | blue solid |
| `PS` | creative / imaginative / inspired | purple solid |
| `PP` | deep / philosophical / profound | purple pulse |

Older docs mention a 7-code scale without the two purple states. Treat the 9-code scale as the newer direction.

The v3 repo includes `v3_WAVESHARE/scripts/validate_protocol.py` to check that the bridge, Android app, firmware self-test list, v3 protocol docs, and this spec still agree on the same 9 commands.

### BLE Protocol

The v1 CPB system uses Nordic UART Service.

UUIDs:

- Service: `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`
- RX, phone writes to device: `6E400002-B5A3-F393-E0A9-E50E24DCCA9E`
- TX, phone reads from device: `6E400003-B5A3-F393-E0A9-E50E24DCCA9E`

Phone to wearable:

```text
GS GP GC RS RF YP BS PS PP   response commands
SR                           request sensor data
SV:*                         servo/debug commands in newer servo firmware
```

Wearable to phone:

```text
VS                           voice start
VS:temp,light,x,y,z           voice start with sensor snapshot
VP                           voice stop
SD:temp,light,x,y,z           sensor data response
S1 / S0                       sensor mode on/off
SC:*                         servo/debug acknowledgement in servo firmware
```

Messages from the wearable are newline-terminated. The phone BLE manager reassembles fragmented BLE packets by buffering until `\n`.

Known naming mismatch to check before debugging BLE:

- Some docs say the board advertises as `WearabLLM`.
- One BLE manager comment mentions `Wearable LLM`.
- Firmware snapshots may differ.

Keep the device name synchronized across firmware and app before assuming BLE is broken.

### v1 Firmware

Primary historical firmware paths:

- `WearabLLM/cpb/code.py`
- `WearabLLM/cpb/code_reveal.py`
- `servo_bluefruit/WearabLLM_Codex/cpb/code.py`
- `servo_bluefruit/WearabLLM_Codex/cpb/code_reveal.py`

The historical CPB firmware handles:

- BLE advertising and UART service
- LED animations on the CPB's 10 built-in NeoPixels
- speaker earcons
- Button A as a tap-to-start/tap-to-stop voice trigger
- slide switch as a sensor-sharing toggle
- sensor reads: temperature, light, accelerometer

The newer servo firmware also handles:

- servo positions for closed/listening/open states
- configurable hold time and tempo
- optional WAV clip playback
- servo acknowledgements back to the app

Servo should be considered experimental/legacy for the next phase unless intentionally revived.

### Desktop App

Path: `servo_bluefruit/WearabLLM/desktop-app/`

Stack:

- Electron
- React/Vite renderer

Purpose:

- desktop companion/scaffold for testing the same LLM-to-command protocol
- BLE connection to the wearable
- typed text input rather than voice
- support for servo-oriented commands such as `SV:LISTEN`, `SV:HOLD=<ms>`, and `SV:CLOSE`

This is useful as a debugging harness if mobile builds are slow or blocked.

### Older Laptop Bridge

Path: `bridge.py` in the project snapshots.

Historical role:

- laptop mic records speech
- local Whisper transcribes
- Claude API returns LED command
- command sent to CPB over USB serial, later BLE

This was the original proof of concept before the phone app took over as the main bridge.

## Where The Project Came From

### Phase 1: USB Serial Proof Of Concept

The first working loop was:

```text
laptop mic -> local Whisper -> Claude API -> serial command -> CPB LEDs
```

The initial LED protocol used simple color commands for yes/no/maybe/neutral. This proved the core interaction: a spoken prompt could produce a physical wearable response.

### Phase 2: BLE And Richer Response Language

The device moved from USB serial to BLE so the CPB could run wirelessly on LiPo battery. The response language expanded into multiple two-character states with different animations and speaker earcons.

The system became:

```text
voice input -> bridge/app -> LLM -> BLE command -> CPB LEDs + speaker
```

### Phase 3: Servo Reveal Experiment

A servo-driven reveal panel was explored so each answer could feel like a small physical event. The code supports servo open/listen/close positions and configurable response hold time.

Current direction: remove this as a primary focus. The project should prioritize voice input, LEDs, compact display, and sound over mechanical actuation.

### Phase 4: Phone App Bridge

The phone app became the intended daily-use bridge:

```text
phone mic -> native speech recognition -> LLM API -> BLE -> wearable
```

This removed the laptop from the normal interaction loop, but still depends on the phone for audio capture.

## Where The Project Is Going

### Near-Term Target

Build the next version around board-native voice input instead of phone-mic input.

Current v3 architecture:

```text
Waveshare BOOT or Hi ESP -> onboard mic -> ESP32-S3 Wi-Fi -> local bridge -> STT/contextual LLM -> LEDs + Verse TTS
```

The board for the current prototype is chosen: Waveshare ESP32-S3-AUDIO-Board. The core audio/bridge/LED/speaker loop is working; current work should build on it without regressing the verified path.

Keep these v3 phase boundaries:

1. Preserve the verified BOOT/wake-word -> mic -> bridge -> LED/TTS loop.
2. Validate the local durable-memory extraction policy and bounded retrieval alongside the existing in-session 20-turn history; keep cloud sync gated on remote forget support.
3. Verify TFT wiring independently before enabling the normal display path.
4. BLE/phone-hosted provisioning remains later work; current Wi-Fi configuration writes local firmware config for the next flash, not live over-the-air device provisioning.

Future board or wearable revisions should still be judged by:

- microphone support, likely I2S or PDM
- BLE support if the phone remains the app/API bridge
- enough memory and timing headroom for audio capture plus LED control
- easy NeoPixel output
- battery compatibility
- stable development workflow
- optional Wi-Fi if audio is streamed to a laptop/local server instead of the phone

### LED Strip Direction

For v3, the Waveshare board's 7 onboard RGB LEDs are the primary response surface. If moving to a smaller wearable board later, replace the CPB's built-in 10-pixel ring with a small addressable RGB strip or ring.

Proposed mapping:

- hue = semantic category from the existing response scale
- count of lit LEDs = confidence/certainty
- animation = tone/intensity
- brightness = state or emphasis, with an accessibility-friendly cap

This means the LLM output contract likely needs to evolve from only:

```text
code + short answer
```

to something like:

```text
code + confidence + optional animation/sound + short answer
```

The old 2-character command should remain supported during transition so current firmware and app can still be tested.

### Microphone Direction

The main design answer for v3 is: capture audio on the board, run open-ended STT off-board.

Relevant options:

1. Board mic, WAV uploaded over Wi-Fi to the local bridge. This is the current v3 phase-1 path.
2. Board mic, WAV uploaded to an Android-hosted bridge later.
3. Board mic for wake/voice activity only, phone still records full speech.
4. Local wake-word detection through ESP-SR WakeNet9, which is now implemented as `Hi ESP`.

The board has native microphones and codecs, but not native open-ended STT for arbitrary questions. Use OpenAI transcription or local Whisper on the bridge for phase 1. Treat ESP-SR/ESP-Skainet as a future wake word or fixed-command layer, not a replacement for open-ended Whisper-style transcription.

## Suggested Next Milestones

1. Physically exercise K1/+ and K3/- and confirm 10-point changes across multiple TTS responses.
2. Decide whether volume should persist in NVS across reboots; it currently resets to the configured boot default.
3. Evaluate auto-extraction quality, add correction-aware replacement, then build an authenticated Home Assistant adapter; keep personal records local-only until remote deletion has parity.
4. Finish TFT perfboard wiring and continuity checks.
5. Flash the display-test variant only when ready to test the TFT wiring, then confirm color bands/readable text.
6. Enable and verify the normal TFT status/response display without changing the working audio path.
7. Later, evolve the response schema to include confidence/display/audio metadata while preserving the 9-command compatibility layer.

## Open Decisions

- Should the final form be the Waveshare board, a smaller wearable board, or a two-device system?
- Should the Android phone eventually host the bridge/STT/API path, or should a laptop/local server remain acceptable?
- Should audio stay over Wi-Fi, move to BLE for control only, or use BLE provisioning plus Wi-Fi audio?
- Which physical PTT control should replace the BOOT button for the final build?
- Should the TFT remain a small response display or become a richer interaction surface?
- How should BLK/backlight and future potentiometer/extra controls be routed on the perfboard adapter?
- Should confidence be model-reported, heuristic, or mapped from code category?
- Should sound be generated tones, cached WAV clips, bridge TTS, or a mix?
- Should physical volume persist in NVS, and should K2/set become mute, session reset, or another control?
- How should Supermemory retrieval be scoped, ranked, and exposed to the user for reset/forget operations?
- Does sensor context still matter in the Waveshare version?
- Should Wi-Fi provisioning become BLE or SoftAP based so firmware can be updated without reflashing?

## Agent Handoff Notes

- Start by reading this file, then inspect `v3_WAVESHARE/README.md`, `v3_WAVESHARE/docs/STATUS.md`, and `v3_WAVESHARE/docs/BRINGUP.md` for current work.
- For project history, inspect `v1/docs/session-log.md`, `v1/phone-app/src/api/llm.ts`, and the older `servo_bluefruit/WearabLLM_Codex/` snapshot if present.
- If `WearabLLM/` files fail to read with `Operation timed out`, check whether they are cloud placeholders and use the readable duplicate snapshots.
- Do not assume the servo is still a requirement. Treat it as legacy unless the user explicitly asks to revive it.
- Do not break the existing 9-command response scale unless replacing it with an intentional migration plan.
- Run `v3_WAVESHARE/scripts/validate_protocol.py` after changing command names, meanings, prompt text, bridge parsing, app command definitions, firmware command handling, or protocol docs.
- Before changing BLE code, remember BLE is v1/current-future provisioning work; v3 phase 1 uses Wi-Fi HTTP to the bridge.
- Before changing the LLM prompt, update the parser and firmware/app command schema together.
- Prefer a narrow, testable hardware loop for each step: dry-run bridge before live STT, saved WAV before live LLM, LED command before display, display before speaker/TTS.
- Do not flash/reset a connected physical board while the user is actively wiring unless explicitly asked.
- Treat Wi-Fi credentials, API keys, saved captures, serial logs, and build outputs as local ignored bench artifacts unless the user explicitly asks to preserve or publish them.

## Glossary

- CPB: Adafruit Circuit Playground Bluefruit.
- BLE: Bluetooth Low Energy.
- NUS: Nordic UART Service, used as the BLE serial-like transport.
- NeoPixel: Addressable RGB LED family used by the CPB and proposed external strip.
- Earcon: Short nonverbal sound cue.
- Bridge: Software layer that connects speech input, LLM API, and wearable output.
- BSSID/AP MAC: Specific Wi-Fi access point MAC used to pin the ESP32-S3 to one radio on multi-AP networks.
- STT: Speech-to-text.
- TTS: Text-to-speech.
