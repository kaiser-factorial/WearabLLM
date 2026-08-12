# WearabLLM v3 — Waveshare Sphere

The active WearabLLM implementation for the Waveshare ESP32-S3-AUDIO-Board,
Android companion, local web dashboard, and hosted agent.

## Working system

```text
Waveshare / Android / Web console
              │
              v
authenticated private Hugging Face Space
              ├─> OpenAI STT + assistant + TTS
              └─> Supabase conversation + config + action queue + memory
```

The Waveshare supports BOOT-button push-to-talk and local `Hi ESP` wake-word
activation. It captures onboard audio, sends a WAV to the hosted bridge, then
responds with the seven-pixel RGB ring, a TFT200C 240x320 display, and ES8311
speaker playback.

Android and the browser share the same conversation and can optionally target
the Waveshare. Targeted messages remain queued until the board claims them and
are marked played only after display/TTS finishes successfully.

Sphere also has bounded model-facing tools for passive body status, hybrid
household memory, read-only source inspection, cross-body expression, and
capability-driven sensors. The external `ducati-temp-sensor` ESP32-S3 body can
register sensors, return authenticated readings, and service bounded scheduled
loops without exposing an inbound home-network port.

The laptop bridge is retained for development but is not required during normal
hosted conversations. The dashboard is still served locally.

## Components

| Path | Purpose |
|---|---|
| `firmware/` | ESP-IDF 5.5 firmware |
| `bridge/` | Local/hosted Python agent and API |
| `app/` | Expo Android companion |
| `transcript_viewer/` | Local Sphere dashboard and authenticated proxy |
| `hosted_agent/` | Hugging Face Docker Space |
| `protocol/` | Shared HTTP/command contract |
| `scripts/` | Build, flash, deploy, smoke, and bring-up tooling |
| `docs/` | Architecture, model tools, pin map, bring-up, and current status |

## Current hosted defaults

```text
provider: OpenAI
STT:      gpt-4o-mini-transcribe
LLM:      gpt-5.4-mini
TTS:      gpt-4o-mini-tts
voice:    marin
```

Supabase is the hosted backend for conversation sessions/turns, archives,
agent settings, compact durable memory, richer memory schema, device actions,
and transcript events.

The hosted tool loop is bounded to eight rounds. Public conversation metadata
contains concise tool activity only; bounded raw tool context is restored
privately for later model turns. Completed user/assistant exchanges use one
bulk insert so a partial write cannot leave a new orphan user turn.

## Firmware

Current capabilities:

- ES7210 microphone capture at 16 kHz
- WakeNet9 `Hi ESP` detection
- GPIO0/BOOT hold-to-talk
- nine-command RGB response language
- ST7789 TFT status and sentence/chunk response cards
- ES8311 TTS playback and K1/K3 volume control
- background transcript logging
- authenticated hosted query, TTS, action-poll, and acknowledgement requests
- certificate-bundle validation for all HTTPS clients
- next-chunk TTS prefetch while the current chunk plays
- PSRAM-backed action response buffer for longer replies
- pre-flash binary/config/source-coherence gate

Configure local ignored values without publishing credentials:

```bash
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
```

Build and flash:

```bash
./scripts/firmware_build.sh
./scripts/firmware_flash_monitor.sh /dev/cu.usbmodemXXXX
```

The direct-OpenAI firmware profile remains an optional development fallback,
but the hosted profile avoids embedding an OpenAI key in the board.

## Hosted bridge

Install local dependencies and run tests:

```bash
python3 -m venv bridge/.venv
bridge/.venv/bin/pip install -r bridge/requirements.txt
OPENAI_API_KEY=test-key bridge/.venv/bin/python -m unittest discover \
  -s bridge -p 'test_*.py'
```

Deploy selected bridge files to a private Space:

```bash
python3 scripts/deploy_hf_space.py \
  --repo-id YOUR_HF_ACCOUNT/wearabllm-agent
```

Required Space secrets:

```text
OPENAI_API_KEY
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
WEARABLLM_DEVICE_TOKEN
```

Set `WEARABLLM_PRINCIPAL_ID` as a Space variable. The deploy helper never reads
or uploads firmware `sdkconfig` or local secrets.

## Dashboard

```bash
cd ..
~/.local/bin/dev start dashboard --detach --json --yes
```

Open `http://127.0.0.1:8787`. The local Python proxy adds device authentication
upstream; browser JavaScript never receives the token.

The dashboard includes body presence, normal chat ordering, safe lightweight
Markdown, optimistic sends, inline thinking, conversation history,
new/rename/archive controls, live model and TTS settings, optional Waveshare
delivery, and a Sensor tab. Any current or archived conversation can be
downloaded as standalone HTML, structured JSON, or plain text. At extreme
zoom-out, messages remain inside a centered 1,180px reading lane.

## Android

```bash
cd app
npm install
npm run typecheck
npm run test:protocol
npm run android
```

The Android native directory is generated and ignored. See `app/README.md` for
JDK/SDK and release-build details.

## Verification

Hardware last verified on 2026-08-09; software last verified on 2026-08-12:

- the user confirmed a physical Waveshare voice interaction works
- hosted query/TLS and Supabase queue polling are live
- dashboard-to-Waveshare display/TTS delivery works
- Android connects to hosted Sphere and shares conversation state
- all 149 bridge tests pass on Python 3.12
- six synthetic sensor protocol routes pass
- Android typechecking and protocol tests pass (retained 2026-08-09 evidence)
- firmware builds, passes the image gate, flashes, and boots on hardware
  (retained 2026-08-09 evidence)
- all 15 Supabase migrations through `20260812010000` match remote
- live health reports Supabase conversation/memory/action backends, hybrid
  memory, source tools, and an eight-round tool limit

See `docs/STATUS.md` for the current verified/not-yet-verified boundary and
`docs/PINMAP.md` for hardware wiring. See `docs/TOOLS.md` for every model tool,
its authorization boundary, and the deferred deployment decisions.

## Security

- Never commit `firmware/sdkconfig`, `.env`, Wi-Fi credentials, tokens, keys,
  service-role credentials, binaries, captures, or build output.
- Keep the Space private and device-authenticated.
- Do not expose the Supabase service role to firmware, Android, or browser code.
- Treat firmware-held device credentials as extractable if hardware is lost.
- Replace the current shared token with per-device credentials before broader
  household deployment.

## Next priorities

1. Publish the dashboard from an authenticated hosted surface.
2. Add signed/versioned Android distribution.
3. Add dual-slot authenticated OTA firmware updates.
4. Add per-device credentials and richer delivery diagnostics.
5. Build the household-memory review UI and monitor exact-scan vector latency
   before deciding whether the small corpus needs an HNSW index.
