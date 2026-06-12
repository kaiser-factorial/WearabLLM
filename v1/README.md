# Claude Wearable

A wearable device that turns your voice into light. Ask a question, and an AI interprets your intent and responds through colored LED animations on an Adafruit Circuit Playground Bluefruit.

**Speak a question > AI processes it > the wearable responds with light and sound.**

```
[Voice] --> [Phone App] --> [LLM API] --> [BLE] --> [CPB LEDs + Speaker]
               ^                                         |
               |      [Sensor data: temp, light, accel]  |
               +------------------------------------------+
```

---

## What It Does

Tap the button (on the board or in the app), speak your question, tap again to send. The AI picks a color and animation that matches the meaning of its answer:

| Code | Visual | Meaning |
|------|--------|---------|
| `GS` | Green solid | Yes, confident |
| `GP` | Green pulse | Yes, gentle |
| `GC` | Green chase | Yes, enthusiastic |
| `RS` | Red solid | No, firm |
| `RF` | Red flicker | Warning / urgent |
| `YP` | Yellow pulse | Uncertain / maybe |
| `BS` | Blue solid | Neutral info |
| `PS` | Purple solid | Creative / imaginative |
| `PP` | Purple pulse | Deep / philosophical |

The AI's full text response is shown on the phone screen alongside the LED animation.

---

## Architecture

The system has three layers that are each independent and swappable:

### 1. Phone App (React Native / Expo)

- **Voice**: On-device speech recognition via iOS/Android native APIs. No audio leaves the phone.
- **LLM**: Multi-provider abstraction (`llm.ts`) supports Anthropic (Claude), OpenAI, and Ollama (local). All providers share the same system prompt and response parsing.
- **BLE**: Nordic UART Service for bidirectional communication with the CPB. Packet reassembly handles fragmented BLE messages.

### 2. LLM Provider Layer

The app sends the transcript (plus optional sensor context) to whichever LLM is configured. The system prompt instructs the model to respond with a 2-character LED code on line 1, followed by a short conversational answer. Any instruction-following model works.

| Provider | Endpoint | Auth | Use Case |
|----------|----------|------|----------|
| Anthropic | `api.anthropic.com/v1/messages` | `x-api-key` | Best response quality |
| OpenAI | `api.openai.com/v1/chat/completions` | `Bearer` token | Alternative cloud option |
| Ollama | `localhost:11434/api/chat` | None | Free, private, local |

### 3. CPB Firmware (CircuitPython)

- **Button A**: Toggle voice recording (tap to start, tap to stop)
- **Slide switch**: Enables/disables sensor data sharing
- **Sensors**: Thermistor (with -5C offset for processor heat), ambient light (scaled to 0-100%), LIS3DH accelerometer
- **BLE UART**: Receives 2-byte LED commands, sends voice signals and sensor data

### Communication Protocol

```
Phone --> CPB:  GS, GP, GC, RS, RF, YP, BS, PS, PP  (LED commands)
                SR                                     (sensor request)

CPB --> Phone:  VS / VS:temp,light,x,y,z              (voice start +/- sensors)
                VP                                     (voice stop)
                SD:temp,light,x,y,z                    (sensor data response)
                S1 / S0                                (sensor mode on/off)
```

All messages are newline-terminated for packet reassembly over BLE.

---

## Hardware

| Component | Purpose |
|-----------|---------|
| [Adafruit Circuit Playground Bluefruit](https://www.adafruit.com/product/4333) | 10 NeoPixel LEDs, speaker, BLE, sensors |
| LiPo battery | Wireless power |

### Sensor Libraries (copy to `CIRCUITPY/lib/`)

- `adafruit_ble/` (included with CircuitPython)
- `neopixel.mpy`
- `adafruit_lis3dh.mpy`
- `adafruit_thermistor.mpy`
- `adafruit_bus_device/`

---

## Getting Started

### Phone App

```bash
cd phone-app
npm install
npx expo run:ios    # or run:android
```

1. Open the app, go to Settings
2. Choose your LLM provider (Anthropic, OpenAI, or Ollama)
3. Enter your API key (not needed for Ollama)
4. Power on the CPB, tap **Scan** in the app
5. Tap the speak button (or press Button A on the CPB), ask a question, tap again
6. Watch the LEDs respond

### CPB Firmware

1. Flash [CircuitPython 10.x](https://circuitpython.org/board/circuitplayground_bluefruit)
2. Copy sensor libraries (see above) into `CIRCUITPY/lib/`
3. Copy `cpb/code.py` to `CIRCUITPY/code.py`
4. The CPB advertises as `"Claude Wearable"` over BLE

### Ollama (Local/Free)

To run a local model with no API key:

```bash
brew install ollama
ollama run hermes3
```

In the app Settings, select Ollama and enter your Mac's IP (e.g. `http://10.0.0.5:11434`).

---

## Project Structure

```
ClaudeWearable/
+-- cpb/
|   +-- code.py              # CPB firmware (BLE + LEDs + sensors + speaker)
+-- phone-app/
|   +-- src/
|   |   +-- api/
|   |   |   +-- llm.ts       # Multi-provider LLM abstraction
|   |   +-- ble/
|   |   |   +-- BLEManager.ts  # BLE scanning, UART, packet reassembly
|   |   |   +-- commands.ts    # LED command types and descriptions
|   |   +-- audio/
|   |   |   +-- VoiceListener.ts  # Speech recognition (iOS 26 polling workaround)
|   |   +-- storage/
|   |   |   +-- apiKey.ts     # Provider config + API key secure storage
|   |   +-- screens/
|   |       +-- HomeScreen.tsx   # Main UI (voice, BLE, session log)
|   |       +-- SettingsScreen.tsx  # Provider picker, API keys, response guide
|   +-- app.json
|   +-- patches/              # Native module patches (expo-speech-recognition)
+-- bridge.py                 # Legacy laptop bridge (Whisper + Claude + BLE)
+-- docs/
```

---

## Design Decisions

### Why multi-provider?

The LED command format (2-char code + short explanation) is model-agnostic. Any instruction-following LLM can handle it. Supporting multiple providers lets you:
- Use Claude for best quality
- Use GPT-4o as an alternative
- Use Ollama for free, fully private, offline operation

### Why toggle instead of hold-to-talk?

Both the phone button and CPB Button A use tap-to-start, tap-to-stop. This matches better when you're wearing the device and can't easily hold a button, and keeps the interaction consistent across both input methods.

### Why on-device speech recognition?

No audio leaves the phone. The transcript is sent as text to the LLM API, keeping voice data private. This also means lower latency since there's no audio upload step.

### Why sensor data is optional?

The slide switch on the CPB controls whether sensor readings are attached to voice messages. This gives users control over what context the AI receives, and keeps API calls smaller when sensors aren't needed.

---

## iOS 26 Notes

Two workarounds are needed for iOS 26 compatibility:

1. **Speech recognition**: `expo-speech-recognition` uses JSI event listeners that crash on iOS 26. A polling-based workaround (`pollEvents` every 150ms) replaces the listener pattern. Applied via `patch-package`.

2. **RCTAppearance crash**: `UIApplication.keyWindow` returns nil on iOS 26, causing a JSI assertion failure. A swizzle in `AppDelegate.mm` patches `RCTAppearance.getColorScheme` to return `"dark"`. This must be re-applied after every `expo prebuild --clean`.

---

## Created by

**Corina Kaiser** — Design, hardware, & vision
**Claude** — Code, architecture, & debugging

Built with Expo, React Native, CircuitPython, and a lot of patience with iOS 26.
