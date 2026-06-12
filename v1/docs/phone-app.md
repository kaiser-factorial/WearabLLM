# Phone App — Claude Wearable

The phone app replaces the laptop bridge entirely. Your phone handles the mic,
speech-to-text, Claude API, and BLE — the microcontroller only drives the LEDs.

## Architecture

```
Phone App
  ├── Mic (native device STT)
  │     ↓ transcript
  ├── Claude API (claude-opus-4-6)
  │     ↓ 2-byte LED command
  └── BLE UART write → Microcontroller → NeoPixels
```

## Stack

| Component | Library |
|---|---|
| Framework | Expo (SDK 52) + TypeScript |
| BLE | `react-native-ble-plx` |
| Speech-to-text | `@react-native-voice/voice` (native OS STT) |
| API key storage | `expo-secure-store` (Keychain/Keystore) |
| Navigation | React Navigation native stack |

---

## Project Structure

```
phone-app/
├── App.tsx                        — root navigation
└── src/
    ├── ble/
    │   ├── BLEManager.ts          — scan, connect, write (singleton)
    │   └── commands.ts            — LED command constants + types
    ├── api/
    │   └── claude.ts              — transcript → LED command via Claude API
    ├── audio/
    │   └── VoiceListener.ts       — push-to-talk native STT wrapper
    ├── storage/
    │   └── apiKey.ts              — secure Anthropic API key storage
    └── screens/
        ├── HomeScreen.tsx         — main UI: connect + hold-to-speak
        └── SettingsScreen.tsx     — API key entry
```

---

## BLE Protocol

The app connects to any device advertising the **Nordic UART Service (NUS)**
with the name `"Claude Wearable"`.

| Role | UUID |
|---|---|
| NUS Service | `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` |
| RX (phone writes) | `6E400002-B5A3-F393-E0A9-E50E24DCCA9E` |
| TX (phone reads) | `6E400003-B5A3-F393-E0A9-E50E24DCCA9E` |

### Commands

The app writes a 2-byte ASCII code to the RX characteristic:

| Code | Meaning |
|---|---|
| `GS` | green solid — yes, confident |
| `GP` | green pulse — yes, gentle |
| `GC` | green chase — yes, enthusiastic |
| `RS` | red solid — no, firm |
| `RF` | red flicker — warning / urgent |
| `YP` | yellow pulse — uncertain |
| `BS` | blue solid — neutral info |

---

## First-Time Setup

### 1. Prerequisites

- Node.js 18+
- Xcode (iOS) or Android Studio (Android)
- Expo CLI: `npm install -g expo-cli`

### 2. Install dependencies

```bash
cd "phone-app"
npm install
```

### 3. Build a dev client (required — native BLE module)

> Expo Go does NOT support BLE. You must build a dev client.

```bash
# iOS (requires Xcode + Apple Developer account)
npx expo run:ios

# Android
npx expo run:android
```

Or use EAS Build (no local Xcode needed):
```bash
npm install -g eas-cli
eas build --profile development --platform ios
```

### 4. Add your API key

Open the app → tap ⚙️ → paste your Anthropic API key.
The key is stored in iOS Keychain / Android Keystore.

---

## Usage

1. Power on your CPB (it will show a dim blue pixel while advertising)
2. Open the app → tap **Scan** — it connects automatically when it finds `"Claude Wearable"`
3. The status dot turns green when connected
4. **Hold** the big button and speak
5. Release — the app transcribes, asks Claude, and sends the LED command
6. Watch the NeoPixels!

---

## Microcontroller Compatibility

Any board that:
- Advertises Nordic UART Service (`6E400001-...`)
- Advertises the name `"Claude Wearable"`
- Accepts 2-byte commands on the RX characteristic

Currently supported:
- **Circuit Playground Bluefruit** — `cpb/code.py`
- **ESP32-S3** — planned (Phase 3)

---

## Future: On-Board Mic (Phase 3)

Rather than using the phone mic, audio can originate from the board:

| Board | Approach |
|---|---|
| CPB (nRF52840) | Built-in PDM mic → BLE audio stream → phone STT |
| ESP32-S3 | I2S mic → WiFi → Whisper API → Claude → LEDs (no phone needed) |

The ESP32-S3 standalone mode is the most capable — no phone required at all.
The phone app would shift to a configuration/monitoring role.
