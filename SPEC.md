# WearabLLM Project Spec

Last updated: 2026-07-10

## One-Line Summary

WearabLLM is a physical LLM interface for talking to an assistant and receiving an embodied response through light, sound, and a small optional display.

## Project Intent

The core idea is:

```text
user speaks -> ESP32 captures audio -> cloud or bridge LLM path -> device responds physically
```

The device is meant to feel like a small expressive companion or oracle rather than a general-purpose chatbot screen. The LLM's text answer can still appear in an app, but the primary output language is physical: LED color, LED motion, sound, and potentially haptic feedback.

The project began on an Adafruit Circuit Playground Bluefruit because that board provided many useful capabilities in one package: BLE, NeoPixels, a speaker, buttons, slide switch, temperature/light/accelerometer sensors, and battery-friendly operation. A servo reveal mechanism was later added as a cute physical actuation experiment. The current direction is to remove or de-emphasize the servo and focus on stronger voice capture, LED expression, sound, and a compact display.

## Current User Goals

- Build the v3 prototype on the Waveshare ESP32-S3-AUDIO-Board.
- Use the board's onboard microphone path so users no longer need to speak into the phone for the first hardware loop.
- Use the board's 7-pixel RGB ring as the first valence display.
- Support both BOOT-button push-to-talk and hands-free activation with the on-device `Hi ESP` wake word.
- Operate from home Wi-Fi without requiring a computer-hosted bridge.
- Use one protected cloud-hosted conversation agent for the Waveshare base and
  future wearable bodies; the laptop must not be a runtime dependency.
- Keep only a bounded active conversation in prompt context, consolidate after
  one idle hour, and retain private raw archives separately from durable facts.
- Stream successful transcripts to a private Supabase table without exposing a
  database-admin key to firmware.
- Use the onboard speaker for OpenRouter-hosted TTS, with physical volume controls.
- Add authenticated OTA updates after one final USB partition/updater flash.
- Keep the optional SPI TFT gated off until its perfboard wiring is verified.
- Preserve the existing semantic response scale, but represent it more richly:
  - color communicates response category or tone
  - number of lit LEDs communicates confidence or certainty
  - animation communicates energy, urgency, or nuance
- 86 the servo direction unless there is a strong reason to keep it.

## What Exists Today

### Main Project Trees

The repository root is the active Git root. Project folders are:

- `v3_WAVESHARE/`: active Waveshare firmware, optional Python bridge, Android
  app scaffold, local transcript viewer, protocol docs, and
  bring-up/deployment scripts.
- `supabase/`: private transcript-table migration and token-gated Edge Function.
- `v1/`: readable historical Bluefruit/phone-app baseline.
- `v2_servo_bluefruit/`: local ignored servo-era archive; only its README is
  published by the top-level repository.
- `hardware_tests/`: standalone hardware probes.

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
-> board posts WAV to the protected Hugging Face agent over HTTPS
-> hosted agent uses OpenRouter for transcription, reply, and TTS
-> board applies LED valence command and plays the returned reply
-> board optionally queues transcript/reply upload to Supabase
```

The currently flashed profile uses this hosted path directly over Wi-Fi. The
local bridge implements the same response contract for development; the
direct-OpenAI firmware profile remains an optional fallback.

Current v3 implementation:

- ESP-IDF firmware in `v3_WAVESHARE/firmware`
- local Python bridge in `v3_WAVESHARE/bridge`
- Android-first Expo scaffold in `v3_WAVESHARE/app`
- HTTP protocol docs in `v3_WAVESHARE/protocol`
- hardware bring-up docs in `v3_WAVESHARE/docs`
- bench helpers in `v3_WAVESHARE/scripts`
- Supabase migration and Edge Function in `supabase`

Current v3 firmware capabilities:

- BOOT-button hold-to-talk state machine
- on-device ESP-SR WakeNet9 wake-word detection for `Hi ESP`; BOOT PTT remains available
- configurable PTT GPIO, active level, and internal pull mode for BOOT-button testing or later external-button wiring
- LED states: dim blue idle, dim neutral white listening, bright neutral white thinking, red error, response command color/animation
- Wi-Fi station setup with local ignored `sdkconfig` credentials and optional BSSID/AP MAC pinning
- serial Wi-Fi diagnostics that include connected AP BSSID, channel, RSSI, and auth mode after association
- selectable bridge or direct-OpenAI HTTPS request path
- direct `gpt-4o-transcribe` speech transcription
- direct `gpt-5.4-mini` Responses API command/reply generation with reboot-
  scoped `previous_response_id` context
- direct `gpt-4o-mini-tts` WAV generation
- optional background HTTPS transcript upload to a token-gated endpoint;
  failures do not block device interaction
- prepared private Hugging Face Docker Space scaffold that preserves the bridge
  API and requires a device token on cloud POST requests
- prepared private Supabase `wearabllm_memories` migration for cloud durable
  memory; only the hosted bridge may use the service-role credential
- deployed protected hosted bridge uses OpenRouter for LLM, transcription, TTS,
  and automatic memory extraction; direct firmware OpenAI mode remains a
  separate, optional local profile
- hosted shared conversation uses one-hour idle sessions: only the active
  bounded turn window enters prompts, while completed raw sessions archive
  privately without automatic expiry
- dependency-free localhost conversation console at `http://127.0.0.1:8787`,
  backed by a local Python proxy so device tokens never reach browser code;
  supports multi-device thread view, web replies as `web-console`, and a
  secondary transcript event feed
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

Current Supabase capabilities:

- private `device_transcripts` table migration with RLS enabled and no anon or
  authenticated client policy
- Edge Function validates a rotatable per-device token and writes with a
  server-side Supabase secret key
- token-gated Edge Function reads support bounded incremental transcript
  retrieval for the local viewer
- firmware receives only the function URL and device token, never the database
  service/secret key
- deployment/config helper generates the device token locally
- dependency-free localhost viewer polls every 1.5 seconds through a Python
  proxy bound to `127.0.0.1`; the device token is never exposed to browser code
- RAM-only four-event background queue; offline persistence is not yet implemented

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
- the hosted-agent firmware was rebuilt and flashed to the physical Waveshare
  board on 2026-07-10
- PSRAM and app boot have been observed in serial logs
- ES7210 microphone and ES8311 speaker-driver initialization have been observed
  in serial logs
- the local `Hi ESP` wake-word model is loaded on the physical board
- the board joined the current home Wi-Fi network and received `192.168.86.38`,
  confirming laptop-independent runtime network access
- BOOT/PTT capture and `Hi ESP` wake-word activation both complete the onboard mic -> bridge -> LED/speaker loop
- captured physical-board microphone audio is non-silent and suitable for live transcription
- ES8311 speaker tones and bridge TTS playback work on the physical speaker
- live `verse` TTS returns a valid normalized WAV and plays through the board
- session-context recall has been exercised through the live bridge
- bridge unit suite passes with 61 tests; bridge smoke tests, Android protocol tests, app typecheck, protocol consistency checks, and firmware builds also pass locally
- optional firmware variant builds pass locally for display, display boot self-test, audio-out, and TTS paths
- app-assisted next-flash config covers Wi-Fi, BSSID, PTT, RGB self-test, speaker output, TTS playback, and TFT toggles
- current firmware image has been built, coherence-verified, flashed, and observed booting with `Hi ESP`, 15-second capture, TTS, and K1/K3 button initialization
- direct-OpenAI firmware has been configured, built, and flashed; bridge and
  transcript-logging variants also compile successfully
- direct TTS playback accepts OpenAI's streaming-length 24 kHz mono WAV,
  resamples it to the board's 16 kHz output path, and has been verified through
  the ES8311 speaker on physical hardware
- the localhost transcript viewer is live and has displayed real device rows
- repository privacy audit found no likely passwords, API keys, or private keys
  in the current publishable tree or Git history; secret-bearing local files and
  firmware binaries are ignored
- GitHub PR #1 merged the standalone firmware, Supabase scaffold, and repository
  cleanup into `main` as commit `608fbbf`
- Supabase migration `20260623130000` is applied to project
  `anjwyaatldrjzecwnspq`; the `wearabllm-transcript` Edge Function is active,
  its device secret is set server-side, and an unauthenticated request is
  rejected with HTTP 401
- the matching Supabase endpoint and generated device token remain only in
  ignored local `firmware/sdkconfig`; the resulting firmware was built, flashed,
  and observed enabling background transcript logging after joining home Wi-Fi
- physical-board interactions have been written to `device_transcripts` and
  displayed through the live localhost transcript viewer

Not yet verified or integrated:

- physical TFT wiring, TFT boot self-test output, and normal display driver output
- authenticated ingestion from a separate Home Assistant host
- correction-aware replacement of stale facts and live bench validation of automatic extraction quality
- physical confirmation that K1 and K3 produce the expected 10-point volume changes, although expander initialization, firmware build, flash, and boot are verified
- BLE/SoftAP live provisioning; current device configuration still requires rebuilding and flashing firmware
- a complete hosted physical interaction: wake/PTT, microphone capture,
  OpenRouter request, LED command, audible TTS, and Supabase transcript row
- authenticated OTA updater and two-slot OTA partition layout

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
Waveshare BOOT or Hi ESP -> onboard mic -> home Wi-Fi -> OpenAI -> LEDs + TTS
                                               |
                                               +-> optional private Supabase log
```

The board for the current prototype is chosen: Waveshare ESP32-S3-AUDIO-Board.
The direct firmware profile removes the runtime computer dependency. The local
bridge remains supported for development, local STT, and richer durable-memory
experiments.

Keep these v3 phase boundaries:

1. Preserve both verified bridge fallback behavior and the compiled/flashed
   direct BOOT/wake-word -> mic -> OpenAI -> LED/TTS path.
2. Preserve the deployed private Supabase endpoint and verified physical-board
   transcript ingestion path plus the localhost viewer read path.
3. Add authenticated OTA only after a deliberate two-slot partition migration
   and one final USB flash.
4. Decide whether direct mode needs durable memory beyond reboot-scoped
   `previous_response_id`; keep personal-memory cloud sync gated on deletion parity.
5. Verify TFT wiring independently before enabling the normal display path.

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

The main design answer for v3 is: capture audio on the board and run open-ended
STT through OpenAI directly, with the bridge retained as an optional fallback.

Relevant options:

1. Board mic, WAV uploaded directly to OpenAI over HTTPS. This is the selected
   bridge-free profile.
2. Board mic, WAV uploaded over Wi-Fi to the optional local bridge.
3. Board mic, WAV uploaded to an Android-hosted bridge later.
4. Local wake-word detection through ESP-SR WakeNet9, implemented as `Hi ESP`.

The board has native microphones and codecs, but not native open-ended STT for
arbitrary questions. Direct mode uses OpenAI transcription; bridge mode can use
OpenAI or local Whisper. ESP-SR handles wake-word detection, not arbitrary STT.

## Suggested Next Milestones

1. Publish the verified transcript viewer, Supabase read path, and direct TTS
   compatibility fix.
2. Implement authenticated OTA and migrate to two app slots; retain rollback.
3. Verify power-only direct interaction and K1/K3 volume changes.
4. Decide whether volume and direct conversation state should persist locally.
5. Evaluate whether direct mode needs a durable transcript or conversation
   persistence layer after the current reboot-scoped `previous_response_id`
   path.
6. Finish TFT wiring/testing without changing the working audio path.
7. Later evolve the response schema while preserving the 9-command layer.

## Open Decisions

- Should the final form be the Waveshare board, a smaller wearable board, or a two-device system?
- Should the optional bridge remain a developer feature, or become a selectable
  home-server mode for durable memory and lower key-exposure risk?
- Should audio stay over Wi-Fi, move to BLE for control only, or use BLE provisioning plus Wi-Fi audio?
- Which physical PTT control should replace the BOOT button for the final build?
- Should the TFT remain a small response display or become a richer interaction surface?
- How should BLK/backlight and future potentiometer/extra controls be routed on the perfboard adapter?
- Should confidence be model-reported, heuristic, or mapped from code category?
- Should sound be generated tones, cached WAV clips, bridge TTS, or a mix?
- Should physical volume persist in NVS, and should K2/set become mute, session reset, or another control?
- How should Supermemory retrieval be scoped, ranked, and exposed to the user for reset/forget operations?
- Does sensor context still matter in the Waveshare version?
- Should Wi-Fi provisioning become BLE or SoftAP based?
- Should OTA use a hosted signed image manifest, a local authenticated upload,
  or both?
- What transcript retention/deletion policy should Supabase enforce?

## Agent Handoff Notes

- Start by reading this file, then inspect `v3_WAVESHARE/README.md`, `v3_WAVESHARE/docs/STATUS.md`, and `v3_WAVESHARE/docs/BRINGUP.md` for current work.
- For project history, inspect `v1/docs/session-log.md` and
  `v1/phone-app/src/api/llm.ts`. The full v2 servo archive is intentionally
  local/ignored.
- Do not assume the servo is still a requirement. Treat it as legacy unless the user explicitly asks to revive it.
- Do not break the existing 9-command response scale unless replacing it with an intentional migration plan.
- Run `v3_WAVESHARE/scripts/validate_protocol.py` after changing command names, meanings, prompt text, bridge parsing, app command definitions, firmware command handling, or protocol docs.
- Before changing BLE code, remember BLE is legacy/future provisioning work;
  the current direct profile uses Wi-Fi HTTPS.
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
