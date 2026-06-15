# v3 Architecture

## Goal

Build the Waveshare ESP32-S3-AUDIO-Board into the next WearabLLM hardware base while preserving the original response language from v1.

The v1 system used phone/laptop voice input plus BLE to a Circuit Playground Bluefruit. v3 moves the first interaction surface onto the Waveshare board:

```text
button -> onboard mic -> bridge STT -> LLM -> onboard RGB ring
```

## Does The Board Have Native STT?

No, not for open-ended questions.

The board has native microphones, an ES7210 audio encoder, an ES8311 audio codec, and enough ESP32-S3 capability for audio capture and some local speech features. Waveshare also ships ESP-SR examples, which are relevant for wake words and limited command recognition.

For arbitrary speech-to-text like "what should I do about this idea?", phase 1 should use a host/phone/cloud STT path. The bridge currently supports:

- OpenAI transcription with `gpt-4o-transcribe`
- optional local Whisper through `openai-whisper`
- typed transcript bypass for hardware-loop testing

## Phase 1

Target behavior:

```text
hold pushbutton
white ring while held/listening
capture audio
POST audio/wav to local bridge
bridge transcribes and calls LLM
board receives 2-character command
ring changes color to match valence
```

Current firmware status:

- LED ring works through ESP-IDF `led_strip`
- Wi-Fi station and HTTP client scaffolding are in place
- button hold state machine is in place
- ES7210/I2S audio capture is integrated in `wearabllm_audio.c`
- firmware has been flashed and boot-verified on the physical Waveshare board
- boot logs confirm ES7210 microphone codec initialization
- silent WAV fallback remains only for on-device audio init/read failures

Remaining phase-1 hardware proof is the full Wi-Fi-connected push-to-talk loop from board capture to bridge response. See `docs/STATUS.md` for the current tested state.

## Phase 1.5

Add the SPI TFT after the LED loop is proven.

The bridge already returns `reply`, and the firmware has an optional ST7735 SPI display path behind `CONFIG_WEARABLLM_DISPLAY_ENABLED`. With the display enabled, the firmware-side display task can show:

- listening
- thinking
- response command
- recognized transcript
- short answer text

Keep this disabled while the perfboard wiring is still being checked. The display driver is intentionally optional so LED/mic/bridge bring-up can continue even if the TFT wiring needs adjustment.

## Phase 2

Add device audio output:

- short earcons first
- then TTS playback
- later streaming or cached generated speech

The Waveshare board has speaker hardware, but TTS itself should stay on phone/cloud/host initially. The ESP32-S3 should play the resulting audio, not run a modern neural TTS model locally.

Current firmware has the first earcon step compiled behind `CONFIG_WEARABLLM_AUDIO_OUT_ENABLED`. When enabled, it initializes the ES8311 DAC path and plays a short tone after a successful bridge response. This is a hardware bring-up primitive for the same speaker path that TTS will use.

The bridge also exposes phase-2 TTS scaffolding at `/v1/tts`. In dry-run mode it returns a valid 16 kHz mono silent WAV; in live mode it calls the configured OpenAI speech model. Firmware can optionally consume this endpoint behind `CONFIG_WEARABLLM_TTS_ENABLED`: after a bridge reply, it posts the reply text to `/v1/tts`, buffers the returned WAV, parses 16 kHz mono 16-bit PCM, and plays it through the ES8311 path. This is compile-tested, but still needs physical speaker validation.

## App Direction

The v1 phone app is Expo and includes Android config, but the repo does not include a generated Android native project. The v1 checked-in native work is iOS-heavy.

For v3, Android should be the primary mobile target:

- Android app as the bridge for STT and API calls
- ESP32 talks to phone over local network first, BLE later if needed
- reuse the v1 LED command parser and response guide

Current app status:

- `app/` is a fresh Expo scaffold intended for Android first
- first screen is a bridge test console for typed transcripts and hold-to-speak Android STT
- the app calls `/v1/query_text` and displays the same `command` + `reply` payload the ESP32 receives
- phone-hosted bridge mode, BLE provisioning, and TTS handoff are still later-phase work

## Response Contract

Keep the two-character command set:

| Code | Meaning | Ring behavior |
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

The Waveshare firmware runs a brief response animation for pulse/chase/flicker commands, then leaves the ring on the command's base color for easy bench verification.
