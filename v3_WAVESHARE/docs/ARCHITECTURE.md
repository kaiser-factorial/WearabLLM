# v3 Architecture

## Goal

Sphere is one household assistant expressed through multiple bodies. The
Waveshare is the embodied home base; Android and Web console are conversation
surfaces. Transport services are infrastructure, not assistant bodies.

## Runtime topology

```text
Android ───────┐
Web console ───┼─> authenticated hosted bridge ─> OpenAI
Waveshare ─────┘                  │
                                 └─> Supabase
                                     ├─ conversation + archive
                                     ├─ agent settings
                                     ├─ durable memory
                                     ├─ device presence/actions
                                     └─ transcript events
```

The default runtime is cloud-hosted. A local bridge remains available for
development and dry-run testing but is not required for normal conversations.

## Bodies

| Body | Stable ID | Capabilities |
|---|---|---|
| Waveshare | `wearabllm-esp32` | Mic, wake/PTT, LEDs, TFT, speaker |
| Android | `wearabllm-android` | Mobile shared chat and delivery control |
| Web console | `web-console` | Local browser chat and agent configuration |
| Wearable | `wearabllm-wearable` | Reserved future body |

Presence is ephemeral and uses a 20-second TTL. Conversation identity is
durable and independent of whether a body is currently online.

## Shared conversation

Every turn is stored with a principal, session, role, and originating device.
The active context is bounded; one hour of inactivity closes and archives the
session. Archived raw turns remain private and are not automatically injected
into future prompts.

Android and Web console use typed queries. Waveshare-originated queries upload
audio for server-side transcription. All three paths converge before assistant
generation so they share one conversation.

## Physical delivery

An assistant reply is not the same as physical playback. When a user enables
Waveshare delivery, the hosted bridge creates a durable action:

```text
created/queued
-> board claims action (dispatched)
-> board renders and starts TTS
-> board acknowledges played or failed
```

Supabase provides persistence and a claim lease so dashboard/Android and the
board can operate concurrently without exposing inbound ports on the ESP32.
The board only makes outbound HTTPS requests.

## Hosted agent

The Python bridge in `bridge/` runs in a private Docker Space. The current
profile uses OpenAI for:

- `gpt-4o-mini-transcribe` STT
- `gpt-5.4-mini` assistant generation
- `gpt-4o-mini-tts` speech synthesis

The bridge validates a rotatable device token before device APIs. OpenAI and
Supabase administrative credentials remain server-side. Agent settings are
stored in Supabase and can be edited through the local dashboard.

## Firmware

The ESP32-S3 owns real-time hardware behavior:

- WakeNet9 wake detection and GPIO0 push-to-talk
- ES7210 audio capture
- HTTP upload/action polling/TTS fetch
- nine-command RGB state language
- ST7789 TFT process and response cards
- ES8311 playback and physical volume control

The firmware validates hosted TLS with ESP-IDF's certificate bundle. Direct
OpenAI firmware mode remains an optional fallback, but the hosted mode avoids
placing an OpenAI key in firmware.

Long replies are split into bounded sentence/chunk actions. The display shows
one chunk while its WAV plays, and firmware prefetches the next WAV to reduce
inter-card delay.

## Dashboard security boundary

The current dashboard binds to `127.0.0.1`. Browser JavaScript talks only to a
local Python proxy, which reads the ignored device token and authenticates
upstream. Hosted deployment must preserve this boundary or replace it with
real user authentication; simply shipping the device token in frontend code is
not acceptable.

Local-only dashboard operations include macOS Keychain updates, firmware
flashing, and HF code deployment. They should be removed or disabled when the
conversation UI is hosted.

## Memory boundary

WearabLLM and the separate local Memory Hub do not share storage or backends.
WearabLLM currently uses compact Supabase memories. The richer
`wearabllm_memory_records` table is a foundation for a future assistant memory
tool with inferred facts, provenance, confidence, supersession, review,
correction, and deletion.

## Response contract

The two-character command set remains the cross-component contract:

`GS`, `GP`, `GC`, `RS`, `RF`, `YP`, `BS`, `PS`, `PP`.

Any change must update firmware, bridge, Android, dashboard, protocol docs,
tests, and `SPEC.md` together.
