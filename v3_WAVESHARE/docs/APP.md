# v3 Android App Notes

The v3 app starts as an Android-first Expo scaffold in `app/`.

## Why A New App Folder?

The v1 app is Expo and already contains Android config, but the checked-in native project is iOS-only. Starting v3 in a separate folder keeps the older app intact while the Waveshare hardware flow changes around onboard mic, speaker, and display.

## Current Flow

```text
typed transcript -> Android app -> bridge /v1/query_text -> command + reply
hold-to-speak -> Android native STT -> bridge /v1/query_text -> command + reply
bridge health check -> Android app -> bridge /health -> active mode/model display
device config form -> Android SecureStore -> optional bridge /v1/device_wifi -> ignored firmware sdkconfig
```

This is not the final app behavior. It is a stable first integration point for the shared response contract while the board firmware is being wired and tested.

The app uses `expo-speech-recognition` for the current hold-to-speak path. This is phone-native STT for app testing; the Waveshare board still uses onboard mic capture plus bridge-side STT for the primary hardware loop.

## Device Config Setup

The current firmware uses build-time Wi-Fi values from ignored
`firmware/sdkconfig`. The app now has a `Device Config` panel that:

- saves SSID/password locally with Expo SecureStore
- optionally saves an AP MAC/BSSID for networks with multiple radios
- saves PTT GPIO, active level, debounce, and pull mode for BOOT-button or external-button wiring
- saves an RGB ring boot self-test toggle for LED bring-up
- saves speaker output, speaker volume, TTS playback, and TTS max-byte settings for later audio bring-up
- saves TFT display and boot self-test toggles for display bring-up
- can send Wi-Fi, PTT, speaker, TTS, and TFT settings to the local bridge
- lets the bridge update ignored `firmware/sdkconfig` for the next build/flash
- refreshes `/health` after a successful config send so staged firmware readback updates immediately

The bridge-side write endpoint is disabled by default. It is enabled by the
dry-run bench helper:

```bash
cd v3_WAVESHARE
./scripts/run_bridge_dryrun.sh
```

Or manually:

```bash
cd v3_WAVESHARE/bridge
python wearabllm_bridge.py --dry-run --allow-device-config
```

After sending settings from the app, rebuild and flash the firmware. This is not
yet true over-the-air provisioning of the running ESP32; that later step should
use BLE provisioning or a temporary SoftAP setup mode.

## Later Flow

Target later behavior:

```text
board PTT -> onboard mic -> bridge/app STT -> LLM -> LED command + display reply
```

Then:

```text
reply text -> app/bridge TTS -> ESP32 speaker playback
```

## Build

```bash
cd v3_WAVESHARE/app
npm install
npm run android
```

Production bundle validation:

```bash
npm run bundle:android
```

This runs Metro's Android export and writes the ignored output under
`app/dist/android`.

The first screen supports:

- bridge base URL storage
- bridge URL normalization when you paste `/v1/query`, `/v1/query_text`, or `/v1/tts`
- bridge health/config check
- bench next-step summary from bridge health, firmware config, and latest audio upload
- bridge audio cap display from `/health`
- latest board audio upload count and WAV summary from `/health`
- firmware config readback from `/health` when bridge device config is enabled
- staged firmware bridge-target match check against the app bridge URL
- one-tap app bridge URL switch to the staged firmware bridge target when they differ
- stale bridge health is cleared whenever the selected target changes
- clean alerts for bridge JSON error responses
- device Wi-Fi/PTT/speaker/TTS/TFT storage and opt-in bridge-assisted firmware config
- dry-run command sequence display from `/health`
- hold-to-speak transcript capture
- typed transcript fallback
- command/reply display
- recent response history

Generate Android native files only when needed:

```bash
npx expo prebuild --platform android
```

Protocol-only checks:

```bash
npm run test:protocol
```
