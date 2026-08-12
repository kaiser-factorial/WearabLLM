# WearabLLM

WearabLLM is an embodied household assistant that shares one conversation
across physical and digital bodies: a Waveshare ESP32-S3 audio board, an
Android companion app, and a web dashboard.

The project began as a phone-to-Adafruit Bluefruit wearable. The active v3
system moves speech capture and physical response onto the Waveshare board,
while a protected hosted agent provides transcription, reasoning, speech, and
shared context.

## Current Status

The v3 end-to-end loop is working on physical hardware:

```text
hold BOOT/PTT or say "Hi ESP"
-> capture speech with the onboard ES7210 microphones
-> send WAV over authenticated HTTPS
-> transcribe and answer with OpenAI
-> persist the shared conversation in Supabase
-> express the response with RGB light, TFT text, and ES8311 speech playback
```

Android and the web dashboard participate in the same Sphere conversation.
Sphere can queue the same semantic expression for any explicit active target:
the Waveshare maps it to LEDs/TFT/speaker, while Android and Web map it to a
colored glow/text and optional local speech.
Device heartbeats distinguish bodies that are online from bodies that are
unplugged or inactive.

The current hosted agent also exposes bounded tools for body status, household
memory, source inspection, cross-body expression, and capability-driven
sensors. The initial external sensor body is the Ducati ESP32-S3 sensor hub;
its firmware lives alongside the Ducati project and registers temperature as a
structured capability rather than hard-coding a temperature-only Sphere tool.

Hardware last verified on 2026-08-09; hosted software and dashboard last
verified on 2026-08-12:

- Waveshare voice request through the hosted agent and audible response
- Android connection and shared chat through Wi-Fi or cellular-capable HTTPS
- dashboard-to-Waveshare queued delivery and playback acknowledgement
- conversation creation, history, rename, and private archive views
- live OpenAI model, TTS model, and voice selection from the dashboard
- Supabase-backed conversations, agent settings, durable-memory substrate, and
  device-action queue
- model-facing memory search/remember/confirm/correct/forget, read-only source
  inspection, cross-body expression, sensor discovery/read/loop/cancel, and
  built-in web search
- bounded private tool context preserved across turns without exposing raw
  results to dashboard clients
- safe Markdown in the dashboard with plain-text projection for Waveshare/TTS
- conversation export as standalone HTML, structured JSON, or plain text
- atomic user/assistant persistence so partial writes cannot create new orphan
  exchanges
- 149 Python bridge tests, six synthetic sensor-route cases, hosted health,
  migration sync, and wide/mobile dashboard QA rerun on 2026-08-12
- Android typechecking/protocol tests, firmware build, image-coherence gate,
  and physical flash/boot retained as verified 2026-08-09 evidence

This remains a private prototype rather than a packaged consumer product. See
[Project boundaries](#project-boundaries) for the important gaps.

## Architecture

```text
┌─────────────────┐
│ Android app     │──┐
└─────────────────┘  │
                     │   authenticated HTTPS
┌─────────────────┐  ├──────────────────────────┐
│ Web dashboard   │──┘                          │
└─────────────────┘                             v
                                      ┌─────────────────────┐
┌─────────────────┐   audio/actions   │ Private HF Space    │
│ Waveshare body  │<─────────────────>│ WearabLLM bridge    │
└─────────────────┘                   └──────────┬──────────┘
┌─────────────────┐   sensor actions            │
│ Ducati sensor   │<─────────────────────────────┤
└─────────────────┘                              │
                                                │
                                      ┌─────────┴─────────┐
                                      v                   v
                                ┌──────────┐       ┌──────────┐
                                │ OpenAI   │       │ Supabase │
                                └──────────┘       └──────────┘
```

The laptop bridge is not required during normal conversations. The laptop is
currently still used to serve the web dashboard locally, deploy the hosted
agent, build Android, and flash/debug firmware.

The reference hosted profile uses:

- `gpt-4o-mini-transcribe` for speech-to-text
- `gpt-5.4-mini` for the assistant response
- `gpt-4o-mini-tts` for speech synthesis
- Supabase for conversation sessions, archived turns, agent settings, compact
  durable memories, richer memory schema, transcripts, and the action queue

The bridge keeps OpenAI, Supabase service-role, and agent configuration secrets
server-side. Firmware and Android receive only a separately rotatable device
token.

## Bodies and Conversation Model

Stable body IDs keep transport infrastructure separate from assistant bodies:

| Body | ID | Role |
|---|---|---|
| Waveshare | `wearabllm-esp32` | Home audio/light/display body |
| Android | `wearabllm-android` | Mobile chat body |
| Web console | `web-console` | Browser chat body |
| Ducati sensor | `ducati-temp-sensor` | Capability-driven physical sensor body |
| Wearable | `wearabllm-wearable` | Reserved future portable body |

All bodies use one principal conversation. Turns retain their originating
`device_id`. Sessions can be renamed or archived; archived raw turns remain
private and do not automatically return to active prompt context.

The action queue makes physical delivery explicit:

```text
queued -> dispatched -> delivered -> rendered -> completed
                                      \-> tts_started -> played
                                      \-> failed
```

Each target has its own action row, expiry, idempotency key, and terminal
status. The UI must not claim `played` until that target acknowledges playback.
Sensor readings follow the same rule: Sphere reports a measurement only after
the authenticated sensor body returns a terminal acknowledgement containing
the requested capability IDs and real values.

## Physical Response Language

WearabLLM preserves the original nine two-character response commands:

| Code | Meaning | Physical response |
|---|---|---|
| `GS` | Yes, confident | Green solid |
| `GP` | Yes, gentle | Green pulse |
| `GC` | Yes, enthusiastic | Green chase |
| `RS` | No, firm | Red solid |
| `RF` | Warning or urgent | Red flicker |
| `YP` | Uncertain or maybe | Yellow pulse |
| `BS` | Neutral information | Blue solid |
| `PS` | Creative or imaginative | Purple solid |
| `PP` | Deep or philosophical | Purple pulse |

These codes are semantic rather than hardware-specific. Android and Web use
the same code to color their Sphere expression surface; local speech on those
bodies is disabled by default and must be opted into in settings.

The TFT shows listening, sending, thinking, error, and response states. Longer
answers are presented one sentence/chunk at a time while firmware prefetches
the next TTS chunk to reduce pauses between cards.

## Repository Layout

| Path | Purpose |
|---|---|
| [`v3_WAVESHARE/`](v3_WAVESHARE/) | Active ESP32, bridge, Android, dashboard, protocol, and tooling |
| [`v3_WAVESHARE/firmware/`](v3_WAVESHARE/firmware/) | ESP-IDF firmware for the Waveshare body |
| [`v3_WAVESHARE/bridge/`](v3_WAVESHARE/bridge/) | Local and hosted Python agent/API implementation |
| [`v3_WAVESHARE/app/`](v3_WAVESHARE/app/) | Expo/React Native Android companion |
| [`v3_WAVESHARE/transcript_viewer/`](v3_WAVESHARE/transcript_viewer/) | Local Sphere dashboard and server-side proxy |
| [`v3_WAVESHARE/hosted_agent/`](v3_WAVESHARE/hosted_agent/) | Private Hugging Face Docker Space image |
| [`v3_WAVESHARE/protocol/`](v3_WAVESHARE/protocol/) | Shared API and command contract |
| [`v3_WAVESHARE/docs/`](v3_WAVESHARE/docs/) | Bring-up, architecture, pin map, and status notes |
| [`v3_WAVESHARE/docs/TOOLS.md`](v3_WAVESHARE/docs/TOOLS.md) | Sphere model tools, safety boundaries, limitations, and deferred decisions |
| [`supabase/`](supabase/) | Database migrations and private backend schema |
| [`v1/`](v1/) | Historical Bluefruit and phone-app baseline |
| [`v2_servo_bluefruit/`](v2_servo_bluefruit/) | Documented servo-era archive |
| [`hardware_tests/`](hardware_tests/) | Standalone hardware probes |

Project intent and longer-term design are documented in [SPEC.md](SPEC.md).
Some deep v3 status documents still describe earlier bring-up phases; the root
README reflects the current verified architecture.

## Getting Started

### Prerequisites

Choose the pieces relevant to the surface you are working on:

- Python 3.12 for the hosted/local bridge
- Node.js and npm for Android
- JDK 17 and Android SDK/NDK for native Android builds
- ESP-IDF 5.5 for firmware
- Supabase CLI for database migrations
- Hugging Face CLI/account for Space deployment

### Run the software checks

Bridge:

```bash
python3 -m venv v3_WAVESHARE/bridge/.venv
v3_WAVESHARE/bridge/.venv/bin/pip install \
  -r v3_WAVESHARE/bridge/requirements.txt
OPENAI_API_KEY=test-key \
  v3_WAVESHARE/bridge/.venv/bin/python -m unittest discover \
  -s v3_WAVESHARE/bridge -p 'test_*.py'
```

Android:

```bash
cd v3_WAVESHARE/app
npm install
npm run typecheck
npm run test:protocol
```

### Run the local dashboard

The dashboard binds to localhost and proxies to the configured hosted or local
bridge so the device token never reaches browser JavaScript.

```bash
cd /path/to/WearabLLM
~/.local/bin/dev start dashboard --detach --json --yes
```

Open `http://127.0.0.1:8787`. If the universal `dev` launcher is not installed,
the project wrapper is:

```bash
./v3_WAVESHARE/scripts/run_transcript_viewer.sh --no-open
```

The server reads the bridge URL and token from ignored firmware configuration,
or accepts explicit `--bridge-url` and `--bridge-token` arguments. See the
[dashboard README](v3_WAVESHARE/transcript_viewer/README.md).

### Build the Android app

Run Expo from the app root:

```bash
cd v3_WAVESHARE/app
export JAVA_HOME="$(/usr/libexec/java_home -v 17)"
export ANDROID_HOME="$HOME/Library/Android/sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
npm run android
```

Fresh installs default to the hosted Sphere URL. The user still supplies the
device token, which is stored in Android SecureStore. Phone-native keyboard
dictation replaces a custom press-to-talk control.

### Configure, build, and flash firmware

Real Wi-Fi credentials and device tokens belong only in ignored
`v3_WAVESHARE/firmware/sdkconfig`. Prefer environment variables so secrets do
not enter shell history:

```bash
cd v3_WAVESHARE
export WEARABLLM_WIFI_PASSWORD='replace-me'
export WEARABLLM_BRIDGE_AUTH_TOKEN='replace-me'

python3 scripts/configure_firmware.py \
  --ssid 'your-wifi' \
  --bridge-url 'https://your-private-space.hf.space/v1/query' \
  --device-id wearabllm-esp32 \
  --disable-direct-openai \
  --clear-openai-api-key \
  --enable-audio-out \
  --enable-tts \
  --enable-display

python3 scripts/configure_firmware.py --status
./scripts/firmware_build.sh
./scripts/firmware_flash_monitor.sh /dev/cu.usbmodemXXXX
```

The pre-flash verifier refuses stale images or binaries containing a different
bridge URL than the staged configuration.

### Provision Supabase and the hosted agent

Apply the schema to a Supabase project:

```bash
supabase link --project-ref YOUR_PROJECT_REF
supabase db push
supabase migration list
```

Configure these as private HF Space secrets:

```text
OPENAI_API_KEY
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
WEARABLLM_DEVICE_TOKEN
```

Set `WEARABLLM_PRINCIPAL_ID` as a Space variable, then deploy only the selected
hosted files:

```bash
python3 v3_WAVESHARE/scripts/deploy_hf_space.py \
  --repo-id YOUR_HF_ACCOUNT/wearabllm-agent
```

The deploy script does not read or upload firmware `sdkconfig`, local captures,
or secrets. See [supabase/README.md](supabase/README.md) and the
[hosted-agent README](v3_WAVESHARE/hosted_agent/README.md).

## Security Model

- The HF Space is private and requires `X-WearabLLM-Device-Token` for device
  APIs.
- OpenAI and Supabase administrative credentials remain server-side.
- Supabase application tables use RLS and are accessed by the hosted bridge's
  service role, not directly by firmware or Android.
- Firmware contains a rotatable device credential and should still be treated
  as extractable if the physical board is lost.
- The present prototype shares one device token between trusted bodies.
  Per-device credentials and revocation are planned before broader household
  access.
- Dashboard JavaScript never receives the device token; its localhost Python
  proxy adds authentication upstream.

Never commit `.env` files, `firmware/sdkconfig`, API keys, Wi-Fi credentials,
Supabase secrets, APKs, compiled firmware, captures, logs, or build output.

Before publishing:

```bash
git status --short
git check-ignore -v v3_WAVESHARE/firmware/sdkconfig
```

## Project Boundaries

The current system is functional, but these items remain intentionally open:

- the dashboard still runs on the laptop rather than from the hosted stack
- Android needs production signing, versioning, and a repeatable distribution
  path
- the Expo 52 dependency tree needs a planned major-version upgrade to resolve
  npm advisories without destabilizing the verified Android build
- firmware updates still require USB; authenticated dual-slot OTA is planned
- one shared device token should become per-device credentials
- the richer household-memory schema is model-accessible for search, safe
  durable remembering, sensitive yes/no confirmation, correction, and forget
  operations, but still lacks a dedicated user
  review interface
- archived conversations cannot yet be restored or deleted from the UI
- long-response playback, retries, lease expiry, and power-loss behavior need
  a recorded regression matrix
- the capability-driven Ducati sensor firmware is maintained in the separate
  `ducati_relay` repository; humidity and light are planned capabilities, not
  currently registered hardware

All 14 migrations through `20260812000000` and the private hosted bridge were
live-checked on 2026-08-12. The current feature set is pushed on draft PR #6;
near-term priorities are to merge the reviewed branch, add a memory review UI,
host the dashboard securely, and add Android distribution and OTA.

## Development Guardrails

- Do not claim a Waveshare action was played until the board acknowledges it.
- Keep the nine response commands synchronized across firmware, bridge, app,
  tests, protocol docs, and `SPEC.md`.
- Use the image-coherence gate before flashing firmware.
- Keep the local Memory Hub project and WearabLLM's Supabase memory completely
  separate; reuse concepts, not storage or backends.
- Run bridge tests, Android typechecking/protocol tests, and a firmware build
  before deployment.
