# Claude Wearable — Project Overview

A wearable device that lets you interact with Claude AI through voice and sensor
input, and receive physical responses via LEDs, sound, haptics, and mechanical
movement on the device.

---

## Concept

Speak a question → Claude processes it → the wearable responds physically.

```
[Voice / Sensors] ──► [Laptop Bridge] ──► [Claude API] ──► [Physical Response]
                                                           LEDs + Sound + Servo + Haptics
```

---

## Hardware

| Component | Purpose | Status |
|---|---|---|
| Adafruit Circuit Playground Bluefruit (CPB) | NeoPixel LEDs, speaker, BLE, sensors | ✅ Working |
| SG90 micro servo | Reveal panel clamshell mechanism | ✅ Have it |
| LiPo battery | Wireless power for CPB | ✅ Working |
| ESP32 38-pin DevKit | WiFi audio input (mic → laptop) | Have it, Phase 3 |
| INMP441 I2S mic | Voice capture on ESP32 | Ordered |
| Vibration motors | Haptic feedback | Ordered |
| Tactile push buttons | Push-to-talk trigger | Ordered |
| Camera module | Visual input to Claude | Have it, Phase 4 |
| Phone / Laptop | Bridge between device and Claude API | Using laptop |

---

## Response Language

Claude picks from 7 states based on content and tone:

| Command | Visual | Sound | Meaning |
|---|---|---|---|
| `GS` | Green solid | Two ascending beeps | Yes, confident |
| `GP` | Green pulse | Single soft tone | Yes, gentle |
| `GC` | Green chase | Three-note rise | Yes, enthusiastic |
| `RS` | Red solid | Two descending beeps | No, firm |
| `RF` | Red flicker | Three rapid low beeps | Warning / urgent |
| `YP` | Yellow pulse | Warble | Uncertain / maybe |
| `BS` | Blue glow | Single mid tone | Neutral info |

---

## Architecture by Phase

### Phase 1 — USB Serial ✅ Complete
```
Laptop mic → Whisper → Claude API → serial byte → CPB NeoPixels
```

### Phase 2 — BLE + Richer Output ✅ Complete
```
Laptop mic → Whisper → Claude API → BLE 2-byte command → CPB (LEDs + earcons)
```
- CPB running on LiPo battery, fully wireless
- 7 response states with animations and multi-note earcons
- Servo reveal panel code written, pending enclosure print

### Phase 3 — Physical Enclosure + Servo 🔄 In Progress
```
Same as Phase 2 + 3D printed chest plate + clamshell panel driven by servo
```
- Design: chest plate + arm sleeve, CPB front, ESP32 back, button at wrist
- `code_reveal.py` ready — tune servo angles once printed

### Phase 4 — Wireless Audio Input (ESP32 + INMP441)
```
ESP32 mic → WiFi → Laptop bridge → Whisper → Claude → BLE → CPB
```
- Replace laptop mic with INMP441 on ESP32
- Push-to-talk button on sleeve end
- Laptop still handles transcription + API calls

### Phase 5 — Haptics
```
Same as Phase 4 + vibration motor patterns as additional output channel
```

### Phase 6 — Camera Input
```
ESP32-CAM → WiFi → Laptop bridge → Claude (vision API) → CPB response
```

### Phase 7 — Full Standalone (Phone Bridge)
```
All hardware ←→ Phone app ←→ Claude API  (no laptop needed)
```

---

## File Structure

```
Claude Wearable/
├── bridge.py              # Laptop bridge (BLE, Whisper, Claude API)
├── requirements.txt       # Python dependencies
├── cpb/
│   ├── code.py            # CPB firmware — BLE + LEDs + speaker (active)
│   └── code_reveal.py     # CPB firmware — adds servo reveal panel
└── docs/
    ├── overview.md        # This file — project summary and roadmap
    ├── setup.md           # How to set up and run everything
    ├── hardware.md        # Hardware details, wiring, component reference
    └── session-log.md     # Chronological dev log
```
