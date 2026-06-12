# Session Log

Chronological record of development sessions.

---

## Session 1 — 2026-03-21

### Goals
- Design overall architecture for a Claude-connected wearable
- Get a working proof of concept on the laptop side

### Decisions Made

**Architecture:** 3-layer system — Wearable ↔ Laptop/Phone Bridge ↔ Claude API.
Start with USB serial (not BLE) for simplicity. Add BLE in a later phase.

**Mic strategy:** Use laptop mic for Phase 1 (simpler), switch to CPB's built-in PDM
mic in a later phase once BLE is working. Note: CPB has a built-in mic — no purchase needed for that board.

**Transcription:** Local Whisper (`base` model) — no extra API key, works offline,
accurate enough for conversational questions.

**LED protocol:** Single byte over serial. G=green, R=red, Y=yellow, B=blue.
Claude's system prompt instructs it to always append `LED:<color>` to every response.
Bridge parses this and sends the byte.

**Model:** `claude-opus-4-6` (most capable, best for nuanced yes/no judgements).

### What Was Built

**`bridge.py`** — laptop bridge script:
- Auto-detects CPB via USB VID:PID or port name heuristic
- Records voice from laptop mic using `sounddevice`
- Transcribes with local Whisper (`base` model)
- Sends transcription + optional sensor context to Claude
- Parses `LED:<color>` from Claude's response
- Sends single byte over serial to CPB
- Dry-run mode if no CPB connected

**`requirements.txt`** — Python deps: anthropic, openai-whisper, sounddevice,
soundfile, pyserial, numpy

### What Was Tested
- Bridge ran successfully in dry-run mode ✓
- CPB detected at `/dev/cu.usbmodem1301` ✓
- Voice capture and Whisper transcription working ✓
- Claude API responding correctly with LED commands ✓
- Serial byte sent to CPB ✓ (CPB not yet listening — CircuitPython not installed)

---

## Session 1 continued — debugging CPB serial + LED

### Problems Encountered & Solutions

**Problem 1: Bridge sending to wrong serial port**
When `boot.py` enables `usb_cdc.data`, CircuitPython exposes two USB CDC serial
ports — a console port (lower number, e.g. `usbmodem1301`) and a data port
(higher number, e.g. `usbmodem1303`). Bridge was connecting to the console port.
Fix: collect all matching ports, sort, take the highest-numbered one (data port).

**Problem 2: REPL interrupt killing code.py**
When bridge.py opened the console serial port, CircuitPython interpreted it as
someone opening the REPL and sent a KeyboardInterrupt to stop code.py. LEDs
stayed white (previous state) because code.py never ran.
Fix: use `usb_cdc.data` (data port) instead of console port. The data port does
NOT trigger the REPL interrupt.

**Problem 3: boot.py requires hard reset to take effect**
`boot.py` changes (enabling `usb_cdc.data`) only apply after a full reset of the
board. Until reset, `usb_cdc.data` is `None`. Code must null-check before using it.
Fix: added `data_port = usb_cdc.data` and guard `if data_port is not None`.

**Problem 4: neopixel library missing**
CircuitPython's `neopixel` module is not built-in — it comes from the Adafruit
Library Bundle. The `lib/` folder on CIRCUITPY was empty, causing an ImportError.
Fix: download `adafruit-circuitpython-bundle-10.x-mpy-*.zip` from GitHub releases,
copy `neopixel.mpy` into `/Volumes/CIRCUITPY/lib/`.

### What Was Tested
- Full loop working: voice → Whisper → Claude → LED ✓
- Color-based LED responses working (green/red/yellow/blue) ✓
- Speaker tones working (high pitch = yes, low pitch = no) ✓

---

## Session 2 — 2026-03-27

### Goals
- Switch from USB serial to BLE (cut the cable)
- Expand LED responses beyond simple yes/no colors
- Begin physical design for the wearable enclosure

### BLE Migration

Switched bridge.py from `pyserial` to `bleak` (async Python BLE library).
CPB code.py now uses `adafruit_ble` + Nordic UART Service instead of `usb_cdc.data`.

Key decisions:
- CPB advertises as `"Claude Wearable"` over BLE
- Bridge scans by name, connects automatically on startup
- Dim blue pixel on CPB while advertising; solid blue when connected
- `boot.py` removed — no longer needed without `usb_cdc.data`

Required addition to `CIRCUITPY/lib/`: `adafruit_ble/` folder from the bundle.

**Result:** CPB running on LiPo battery, fully wireless ✓

### Expanded Response Palette

Upgraded from 4 single-byte states to 7 two-byte commands with distinct
animations and multi-note earcons:

| Command | Visual | Sound | Meaning |
|---|---|---|---|
| `GS` | Green solid | Two ascending beeps | Yes, confident |
| `GP` | Green breathing pulse | Single soft tone | Yes, gentle |
| `GC` | Green pixel chase | Three-note rise | Yes, enthusiastic |
| `RS` | Red solid | Two descending beeps | No, firm |
| `RF` | Red flicker | Three rapid low beeps | Warning / urgent |
| `YP` | Yellow slow pulse | Warble (alternating tones) | Uncertain |
| `BS` | Blue soft glow | Single mid tone | Neutral info |

System prompt updated to describe all 7 options. Claude picks based on
both content and tone of the response.

Animation is driven by a non-blocking time-based state machine on the CPB —
the BLE receive loop and animation frame updates run interleaved at ~25fps.

### Hardware Ordered
- **INMP441** I2S MEMS microphone — for ESP32 wireless audio input (Phase 3)
- **Vibration motors** — for haptic feedback (Phase 3)
- **Tactile push buttons** — for push-to-talk trigger on ESP32

### Physical Design Decisions

**Form factor:** Neck/chest plate with single sleeve extending down one arm.
- CPB + servo on front of chest plate
- ESP32 on back
- Soft push button at end of sleeve (push-to-talk)
- 3D printed enclosure, sewn onto fabric sleeve

**Servo — reveal panel concept:**
A clamshell panel covers the NeoPixel ring when idle. When Claude responds:
1. Servo sweeps panel open (eased motion, 0.5s)
2. LEDs animate + earcon plays
3. Panel holds open for 3.5 seconds
4. Servo sweeps panel closed, LEDs off

While idle: dim blue light leak visible around panel edges (heartbeat glow).

This makes every response feel like an event rather than a passive indicator.

### CPB Firmware Files
Two variants now maintained in `cpb/`:
- `code.py` — BLE + NeoPixels + speaker (no servo) — current stable version
- `code_reveal.py` — BLE + NeoPixels + speaker + servo reveal panel — ready to use once enclosure is printed

To switch variants: copy desired file onto `CIRCUITPY/` and rename to `code.py`.

### What's Next
1. 3D print enclosure — chest plate with clamshell panel and servo mount
2. Wire SG90 servo to CPB pad A1, tune `CLOSED_ANGLE` / `OPEN_ANGLE`
3. Test `code_reveal.py` with physical panel
4. Wire INMP441 to ESP32 once it arrives
5. Write MicroPython firmware for ESP32 (WiFi + I2S mic + HTTP POST to bridge)
6. Update bridge.py to receive audio from ESP32 over WiFi
7. Add vibration motor output
