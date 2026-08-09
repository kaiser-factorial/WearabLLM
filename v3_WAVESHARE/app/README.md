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
- Shows active conversations in the drawer and archived conversations behind a
  compact Archive control.
- Provides Rename and Archive through each conversation's `...` menu.
- Stores only the Sphere URL and device token in Android SecureStore.
- Uses phone keyboard dictation; the app has no custom press-to-talk control.

Android/Web prompts use `/v1/query_text`. Waveshare delivery uses
`/v1/interactions` and follows the board-reported lifecycle:

```text
queued -> dispatched -> played
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
cd v3_WAVESHARE/app
npm install
npm run typecheck
npm run test:protocol
npm run android
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
npx expo prebuild --platform android
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
`npm run test:protocol`.
