# WearabLLM Android

Expo/React Native companion for the Sphere shared conversation and Waveshare
body.

## Current behavior

- Uses the hosted Sphere backend over Wi-Fi or cellular.
- Shows live presence for Waveshare, Android, Web console, and the planned
  Wearable. `local-bridge` is infrastructure, not a body.
- Reads and writes the shared Supabase-backed conversation.
- Adds a submitted user message immediately, then shows an inline assistant
  thinking state.
- Optionally queues the reply for Waveshare display and speech.
- Receives targeted Sphere expressions, maps all nine semantic commands to a
  phone glow/text treatment, and optionally speaks them with device-local TTS.
- Renders durable web-search source links beneath assistant turns.
- Shows active conversations in the drawer and archived conversations behind a
  compact Archive control.
- Provides Rename and Archive through each conversation's `...` menu.
- Stores only the Sphere URL and device token in Android SecureStore.
- Uses phone keyboard dictation; the app has no custom press-to-talk control.

Android/Web prompts use `/v1/query_text`. Cross-body delivery uses the action
claim/ack API. Automatic phone speech is disabled by default in Connection
settings. Each target follows its own reported lifecycle:

```text
queued -> dispatched -> delivered -> rendered -> completed
                                      \-> tts_started -> played
                                      \-> failed
```

The app never reports physical playback until the board acknowledges it.

## Connection

Fresh installs default to the hosted Sphere URL. Enter the separately supplied
device token once, then use **Save & test**. The token is sent only in the
`X-WearabLLM-Device-Token` header and must never be committed, logged, or placed
in screenshots.

For a local development bridge, replace the URL with an address reachable from
the phone. `localhost` refers to the phone itself, not the development laptop.

## Development

```bash
cd vC_WAVESHARE/app
pnpm install
pnpm typecheck
pnpm test:protocol
pnpm android
```

Native Android requires JDK 17 and a complete Android SDK/NDK:

```bash
export JAVA_HOME="$(/usr/libexec/java_home -v 17)"
export ANDROID_HOME="$HOME/Library/Android/sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
```

The native `android/` directory is generated and ignored. Recreate it when
needed:

```bash
pnpm exec expo prebuild --platform android
```

Build a bundled release artifact from the generated project:

```bash
cd android
export NODE_ENV=production
./gradlew app:assembleRelease -x lint -x test --build-cache
```

The current release process uses a local development signing configuration. A
production key, versioning policy, and distribution/update channel remain
future work.

## Stable body IDs

| Body | ID |
|---|---|
| Waveshare | `wearabllm-esp32` |
| Android | `wearabllm-android` |
| Web console | `web-console` |
| Future wearable | `wearabllm-wearable` |

Protocol behavior lives in `src/protocol/bridgeClient.ts` and is covered by
`pnpm test:protocol`. This app uses **pnpm** with `node-linker=hoisted` (Expo/Metro
needs a flat `node_modules`). A 1.7 GB `node_modules` is almost always Gradle
intermediates left in `expo-modules-core/android/build` after `pnpm android` —
delete `node_modules` and reinstall rather than treating it as JS bloat.
