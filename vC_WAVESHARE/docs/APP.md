# Android App Notes

The active Android companion lives in `vC_WAVESHARE/app/`. It is an Expo/React
Native client for the shared Sphere conversation, not a firmware-configuration
console.

## Product behavior

```text
typed prompt or keyboard dictation
-> wearabllm-android turn
-> hosted /v1/query_text
-> shared assistant response
-> optional action for wearabllm-esp32
```

The app mirrors the dashboard:

- horizontal body status
- normal chat ordering with immediate user-message insertion
- inline assistant thinking state
- optional **Also play on Waveshare** toggle
- conversation drawer with `+` new session
- compact Archive view
- Rename/Archive behind each row's `...` menu
- connection accordion at the bottom of the drawer

There is no custom press-to-talk control. Voice composition uses Android's
keyboard dictation.

## Authentication

The Connection accordion stores only:

- Sphere base URL
- optional/required device token

Both persist in SecureStore. Hosted requests use
`X-WearabLLM-Device-Token`. Hardware settings such as Wi-Fi, TFT pins, capture
limits, and speaker configuration remain in ignored firmware config.

## Build

```bash
cd vC_WAVESHARE/app
npm install
export JAVA_HOME="$(/usr/libexec/java_home -v 17)"
export ANDROID_HOME="$HOME/Library/Android/sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
npm run android
```

The generated `android/` tree is ignored. Recreate it with:

```bash
npx expo prebuild --platform android
```

Validation:

```bash
npm run typecheck
npm run test:protocol
npm run bundle:android
```

See `../app/README.md` for current release-build and connection details.
