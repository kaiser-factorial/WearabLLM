# Hardware Reference

---

## Adafruit Circuit Playground Bluefruit (CPB)

**Chip:** Nordic nRF52840
**Docs:** adafruit.com/product/4333
**CircuitPython board ID:** `circuitplayground_bluefruit`
**CircuitPython version:** 10.1.4 (stable)

### Built-in Components

| Component | Details | Status |
|---|---|---|
| NeoPixels | 10x RGB LEDs in a ring | ✅ In use — LED response output |
| Speaker | Small built-in piezo | ✅ In use — earcon tones |
| Bluetooth LE | nRF52840 integrated | ✅ In use — wireless bridge to laptop |
| PDM Microphone | Knowles SPH0641LM4H | Future: onboard voice input |
| Thermistor | NTC thermistor | Future: ambient temperature context |
| Light sensor | APDS9960 | Future: ambient light context |
| Accelerometer | LIS3DH | Future: motion / orientation context |
| USB | Micro-USB | Dev only: flashing + REPL |

### NeoPixel Layout
```
        [0]
    [9]     [1]
  [8]         [2]
    [7]     [3]
        [6]
    [5]     [4]  ← indices 0–9, clockwise from top
```

### LED + Sound Response Language (Phase 2)

7 two-byte commands, each with a distinct animation and earcon:

| Command | Animation | Sound | Meaning |
|---|---|---|---|
| `GS` | Green solid | Two ascending beeps | Yes, confident |
| `GP` | Green breathing pulse | Single soft rising tone | Yes, gentle |
| `GC` | Green pixel chase | Three-note ascending melody | Yes, enthusiastic |
| `RS` | Red solid | Two descending beeps | No, firm |
| `RF` | Red flicker | Three rapid low beeps | Warning / urgent |
| `YP` | Yellow slow pulse | Warble (two alternating tones) | Uncertain |
| `BS` | Blue soft glow | Single mid tone | Neutral info |

### External Components (CPB)

| Component | Connection | Purpose |
|---|---|---|
| SG90 micro servo | Signal → pad A1, power → 3.3V or VOUT, GND → GND | Reveal panel clamshell |
| LiPo battery | JST connector on CPB | Wireless power |

### Servo Wiring (SG90)
```
SG90 orange (signal) → CPB pad A1
SG90 red    (power)  → CPB 3.3V  (or VOUT if panel is heavy)
SG90 brown  (GND)    → CPB GND
```
Servo is driven via raw `pwmio.PWMOut` at 50Hz. No extra library needed.
Tune `CLOSED_ANGLE` and `OPEN_ANGLE` in `code_reveal.py` to match your printed enclosure.

### Required CircuitPython Libraries
Must be present in `CIRCUITPY/lib/`:
- `neopixel.mpy` — NeoPixel control
- `adafruit_ble/` — BLE radio + Nordic UART Service (whole folder from bundle)

All from the **Adafruit CircuitPython Bundle 10.x**:
github.com/adafruit/Adafruit_CircuitPython_Bundle/releases/latest

### CPB Firmware Variants

| File | Description | Use when |
|---|---|---|
| `cpb/code.py` | BLE + LEDs + speaker | Daily use, no servo |
| `cpb/code_reveal.py` | BLE + LEDs + speaker + servo reveal panel | Enclosure is printed and wired |

To deploy: copy the desired file to `CIRCUITPY/` and name it `code.py`.

---

## ESP32 (38-pin DevKit, Micro-USB)

**Chip:** ESP32-WROOM-32 (Xtensa LX6 dual-core, 240MHz)
**FCC ID:** 2BB77-ESP32-32X
**Role:** Phase 3 — wireless audio input via WiFi

### Relevant Specs
- WiFi 802.11 b/g/n (2.4GHz)
- Bluetooth 4.2 BLE
- 520 KB SRAM
- I2S peripheral (for INMP441 mic)
- 38 GPIO pins

### INMP441 Mic Wiring (ordered, not yet arrived)
```
INMP441 VDD  → ESP32 3.3V
INMP441 GND  → ESP32 GND
INMP441 SCK  → ESP32 GPIO 26
INMP441 WS   → ESP32 GPIO 25
INMP441 SD   → ESP32 GPIO 34
INMP441 L/R  → ESP32 GND   (selects left channel)
```

### Planned Firmware
MicroPython script:
1. Connect to WiFi on boot
2. Wait for tactile button press (push-to-talk)
3. Record audio from INMP441 via I2S at 16kHz
4. On button release: HTTP POST audio to laptop bridge
5. Bridge transcribes with Whisper + calls Claude + sends BLE to CPB

---

## Vibration Motor

**Status:** Ordered
**Role:** Haptic feedback — supplement or replace audio earcons in quiet environments

### Planned Response Patterns
| Pattern | Meaning |
|---|---|
| 1 short buzz | Yes |
| 2 short buzzes | No |
| 1 long buzz | Warning / urgent |
| Rapid escalating | Very urgent |

Wiring: digital output pin → NPN transistor → motor → power rail.
(Motors draw too much current for direct GPIO drive.)

---

## Tactile Push Button

**Status:** Ordered
**Role:** Push-to-talk trigger on ESP32, positioned at end of sleeve

---

## Physical Design

**Form factor:** Chest/neck plate + single arm sleeve
- CPB + servo on front plate
- ESP32 on back of plate
- Button at wrist end of sleeve
- 3D printed PLA enclosure, sewn onto fabric

**Clamshell reveal panel:**
- Servo drives a hinged panel that covers the NeoPixel ring when idle
- Panel opens on response, closes after hold period
- Light leaks around panel edges during idle (heartbeat glow)

---

## Camera Module

Details TBD — likely OV2640 (common with ESP32-CAM boards). Phase 4+.

---

## Wiring Summary by Phase

### Phase 2 (current)
```
CPB LiPo battery
CPB pad A1 → SG90 servo signal
CPB ←BLE→ Laptop
```

### Phase 3 (next)
```
ESP32 GPIO 25/26/34 → INMP441 mic
ESP32 GPIO x → tactile button
ESP32 GPIO x → vibration motor (via transistor)
ESP32 ←WiFi→ Laptop
CPB ←BLE→ Laptop
```
