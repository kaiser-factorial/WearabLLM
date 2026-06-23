# WearabLLM v3 Android App

Android-first companion app scaffold for the Waveshare hardware version.

The v1 phone app was Expo-based and had Android config, but only the iOS native project was checked in. This v3 app starts as an Expo project without checked-in native folders. Generate Android native files only when needed with:

```bash
npx expo prebuild --platform android
```

## Phase 1 Role

The board talks directly to the bridge over local Wi-Fi for the first hardware loop. This app mirrors that contract so the UI and bridge can be tested before BLE or phone-hosted bridge mode exists:

```text
typed transcript -> POST /v1/query_text -> command + reply
hold-to-speak -> Android native STT -> POST /v1/query_text -> command + reply
bridge check -> GET /health -> active bridge mode/model/audio cap summary
device config setup -> SecureStore -> optional POST /v1/device_wifi for next flash
```

Later phases can add:

- phone-hosted bridge mode
- BLE provisioning/control
- TTS generation and audio playback handoff

## Run

```bash
cd v3_WAVESHARE/app
npm install
npm run android
```

To verify a production Android Hermes bundle without an emulator:

```bash
npm run bundle:android
```

The generated `dist/android` directory is ignored by git.

Set the bridge URL to your computer's LAN IP, for example:

```text
http://192.168.1.23:8765
```

The app posts to:

```text
/v1/query_text
```

Use `Check` after setting the bridge URL to confirm the app can reach the bridge,
verify whether the bridge is in dry-run or live API mode, see the current audio
upload cap, and see the latest board audio upload summary after `/v1/query`.
The health panel also derives a one-line next step for the first dry-run board
loop from those fields.
When device config is enabled, `Check` also shows the
ignored firmware `sdkconfig` readiness, Wi-Fi set/empty state, PTT settings,
staged firmware bridge target, bridge target match, speaker/TTS settings, and
TFT display/self-test state for the next flash. Bridge JSON errors are
surfaced directly in app alerts so configuration problems are easier to
diagnose during bench tests.

## Device Config Setup

The app can store device Wi-Fi credentials, PTT wiring settings, PTT debounce,
speaker/TTS bring-up settings, an RGB ring boot-test toggle, and TFT display bring-up toggles in Android
SecureStore. Wi-Fi supports an optional AP MAC/BSSID for networks with multiple
radios. During bench testing, start the bridge with device config enabled:

```bash
cd ../
./scripts/run_bridge_dryrun.sh
```

Then use `Send To Bridge` in the app's `Device Config` section. The bridge
writes the ignored firmware `sdkconfig`; rebuild and flash the board afterward.
After a successful send, the app refreshes `/health` so the staged firmware
settings and bridge-target match update in the health panel.
If the app URL and staged firmware bridge target differ, the health panel can
switch the app to the staged firmware URL. Editing, saving, or switching the
bridge URL clears the previous health result and requires a fresh `Check`, so
readiness from one computer cannot be displayed against another target.
The AP MAC field is optional; when present, the app expects the standard
six-byte colon-separated form, such as `02:00:00:00:00:01`, and normalizes
uppercase values before sending them. For PTT, the default is `GPIO0`,
active-level `0`, debounce `35 ms`, pull `up`, which matches the BOOT button
and a simple GPIO-to-GND external pushbutton.

For speaker/TTS bring-up, leave `Speaker Output` and `TTS Playback` off for the
first mic/bridge loop. `TTS Playback` turns on `Speaker Output` because firmware
needs the ES8311 output path before it can play bridge WAV responses.

For the TFT, `TFT Boot Test` enables the display and asks firmware to show the
boot color/text wiring check after the next flash. Leave `TFT Display` enabled
and turn off `TFT Boot Test` once the wiring test is no longer needed.

This is a development convenience, not live ESP32 provisioning yet. A later
firmware/app phase should add BLE or SoftAP provisioning so the phone can update
the running device without a reflash.
