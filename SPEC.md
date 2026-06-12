# WearabLLM Project Spec

Last updated: 2026-06-11

## One-Line Summary

WearabLLM is a wearable interface for talking to an LLM and receiving an embodied response through light, sound, and possibly other physical activations.

## Project Intent

The core idea is:

```text
user speaks -> app/bridge transcribes -> LLM interprets -> wearable responds physically
```

The device is meant to feel like a small expressive companion or oracle rather than a general-purpose chatbot screen. The LLM's text answer can still appear in an app, but the primary output language is physical: LED color, LED motion, sound, and potentially haptic feedback.

The project began on an Adafruit Circuit Playground Bluefruit because that board provided many useful capabilities in one package: BLE, NeoPixels, a speaker, buttons, slide switch, temperature/light/accelerometer sensors, and battery-friendly operation. A servo reveal mechanism was later added as a cute physical actuation experiment. The current direction is to remove or de-emphasize the servo and focus on stronger voice capture, LED expression, and sound.

## Current User Goals

- Add an onboard or wearable microphone so users no longer need to speak into the phone.
- Consider moving off the Circuit Playground Bluefruit to a new board better suited for mic input, custom LEDs, and future hardware.
- Replace the CPB's built-in 10-pixel ring with an external NeoPixel strip if changing boards.
- Preserve the existing semantic response scale, but represent it more richly:
  - color communicates response category or tone
  - number of lit LEDs communicates confidence or certainty
  - animation communicates energy, urgency, or nuance
- 86 the servo direction unless there is a strong reason to keep it.

## What Exists Today

### Main Project Trees

There are duplicate or historical project folders:

- `WearabLLM/`: appears to be the intended main app/firmware tree.
- `servo_bluefruit/WearabLLM_Codex/`: contains a fuller older snapshot with readable docs and firmware.
- `servo_bluefruit/WearabLLM/desktop-app/`: contains an Electron desktop scaffold for BLE and LLM command testing.
- `servo_bluefruit/servo_potentiometer_copy/`: older Arduino servo experiment.

Important note for future agents: some files in `WearabLLM/` currently appear as local placeholder files on macOS/iCloud. They have file sizes but zero allocated blocks, and reads may time out. If a file in `WearabLLM/` cannot be read, check the matching file under `servo_bluefruit/WearabLLM_Codex/` or the desktop scaffold.

### Phone App

Path: `WearabLLM/phone-app/`

Stack:

- React Native / Expo
- `react-native-ble-plx` for BLE
- `expo-speech-recognition` for phone-native speech recognition
- `expo-secure-store` for API keys/provider config

Current responsibilities:

- scan for the wearable over BLE
- subscribe to device-originated messages such as button-triggered voice start/stop
- use phone mic and native speech recognition for transcription
- send transcript plus optional sensor context to an LLM
- parse the LLM response into a compact wearable command
- send the command back to the wearable over BLE
- show the LLM's short text answer in the UI

The app has a known iOS 26 workaround: speech recognition event listeners were replaced by polling via `pollEvents()` because the JSI event path was unstable.

### LLM Layer

Path: `WearabLLM/phone-app/src/api/llm.ts`

The phone app has a provider abstraction for:

- Anthropic
- OpenAI-compatible chat completions
- Ollama local models

The LLM prompt asks the model to return:

```text
Line 1: a 2-character LED command
Line 2+: a short conversational answer
```

The parser falls back to `BS` if the command is missing or invalid.

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

### BLE Protocol

The system uses Nordic UART Service.

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

### Firmware

Primary historical firmware paths:

- `WearabLLM/cpb/code.py`
- `WearabLLM/cpb/code_reveal.py`
- `servo_bluefruit/WearabLLM_Codex/cpb/code.py`
- `servo_bluefruit/WearabLLM_Codex/cpb/code_reveal.py`

The CPB firmware handles:

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

Current direction: remove this as a primary focus. The project should prioritize voice input, LEDs, and sound over mechanical actuation.

### Phase 4: Phone App Bridge

The phone app became the intended daily-use bridge:

```text
phone mic -> native speech recognition -> LLM API -> BLE -> wearable
```

This removed the laptop from the normal interaction loop, but still depends on the phone for audio capture.

## Where The Project Is Going

### Near-Term Target

Build the next version around wearable-native voice input instead of phone-mic input.

Candidate architecture:

```text
wearable mic/button -> microcontroller -> phone/app or local bridge -> LLM -> wearable LEDs + sound
```

The exact board is not yet chosen. The decision should be driven by:

- microphone support, likely I2S or PDM
- BLE support if the phone remains the app/API bridge
- enough memory and timing headroom for audio capture plus LED control
- easy NeoPixel output
- battery compatibility
- stable development workflow
- optional WiFi if audio is streamed to a laptop/local server instead of the phone

### LED Strip Direction

If moving to a new board, the built-in CPB LED ring should be replaced by a small NeoPixel strip.

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

The main unresolved hardware/software design question is where speech-to-text should happen.

Options:

1. Mic on wearable, raw or compressed audio streamed to phone.
2. Mic on wearable, audio streamed over WiFi to a laptop/local server.
3. Mic on wearable, wake/voice activity only, phone still records full speech.
4. A board capable of running local/offline speech features, if feasible.

For the immediate prototype, option 1 or 2 is likely more realistic than fully standalone transcription on a small board.

## Suggested Next Milestones

1. Audit hardware on hand and choose the next board/mic path.
2. Freeze a v2 command schema that supports LED color plus confidence.
3. Preserve the existing 9-code command parser as a compatibility layer.
4. Build a minimal NeoPixel strip firmware sketch for the chosen board.
5. Test phone/app BLE command delivery to the new board with typed commands first.
6. Add wearable mic capture and a simple push-to-talk path.
7. Integrate microphone transcript into the existing LLM provider layer.
8. Reintroduce sounds after LED strip behavior is stable.
9. Remove or archive servo-specific UI/firmware paths once no longer needed.

## Open Decisions

- Which board replaces the CPB, if any?
- Should the phone remain the LLM/API bridge?
- Should audio move over BLE, WiFi, or a separate local bridge?
- What mic is available now: INMP441 I2S, CPB built-in mic, or another module?
- How many NeoPixels are in the strip?
- Should confidence be model-reported, heuristic, or mapped from code category?
- Should sound be generated tones, stored WAV clips, or both?
- Does sensor context still matter in the next version?

## Agent Handoff Notes

- Start by reading this file, then inspect `servo_bluefruit/WearabLLM_Codex/README.md` and `servo_bluefruit/WearabLLM_Codex/docs/session-log.md` for history.
- If `WearabLLM/` files fail to read with `Operation timed out`, check whether they are cloud placeholders and use the readable duplicate snapshots.
- Do not assume the servo is still a requirement. Treat it as legacy unless the user explicitly asks to revive it.
- Do not break the existing 9-command response scale unless replacing it with an intentional migration plan.
- Before changing BLE code, verify the advertised device name and Nordic UART UUIDs match on both sides.
- Before changing the LLM prompt, update the parser and firmware command schema together.
- Prefer a narrow, testable hardware loop for each step: typed app command to LEDs before voice, voice transcript before LLM, LLM command before sound.

## Glossary

- CPB: Adafruit Circuit Playground Bluefruit.
- BLE: Bluetooth Low Energy.
- NUS: Nordic UART Service, used as the BLE serial-like transport.
- NeoPixel: Addressable RGB LED family used by the CPB and proposed external strip.
- Earcon: Short nonverbal sound cue.
- Bridge: Software layer that connects speech input, LLM API, and wearable output.
