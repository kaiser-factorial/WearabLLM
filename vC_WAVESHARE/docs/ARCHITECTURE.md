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

## Cross-body expression delivery

An assistant reply is not the same as delivery on another body. When the user
explicitly names one or more targets, the hosted bridge creates one durable
action per target carrying the same device-neutral expression:

```json
{"version":1,"command":"GP","text":"Dinner is ready.","channels":["visual","display","audio"]}
```

```text
queued -> dispatched -> delivered -> rendered -> completed
                                      \-> tts_started -> played
                                      \-> failed
```

Waveshare renders command/text/channels as LEDs, TFT, and hosted TTS. Android
and Web render the same command as a colored Sphere surface plus text; local
speech is an opt-in body preference. Expired actions are never claimed.

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

## Agent tools and memory boundary

WearabLLM and the separate local Memory Hub do not share storage or backends.
WearabLLM retains compact automatic memories for compatibility and exposes the
richer `wearabllm_memory_records` table through model tools. Search is allowed
when relevant. Safe stable facts may be remembered without a magic phrase;
the bridge blocks credentials and requires a bound yes/no confirmation for
precise address or contact data. Correction, forgetting, and cross-body sends
still require matching current-turn intent. Corrections preserve supersession
history. Clients receive bounded tool activity metadata, including a short
content prefix for memory mutations so the user can verify what changed. A
dedicated memory review UI remains future work.

The OpenAI Responses tool loop exposes built-in web search only for explicit or
clearly time-sensitive current requests. Public source URLs are stored as
non-spoken assistant-turn metadata and rendered by Android and Web. Tool rounds
are bounded and parallel function calls are disabled.

Sphere's self-source tools read only a build-time manifest uploaded with the
private hosted bridge. The manifest broadly covers first-party bridge, client,
firmware, protocol, migration, and documentation sources while excluding
secrets, configuration, build outputs, captures, and arbitrary filesystem
paths.

Rich-memory retrieval is hybrid: the hosted bridge creates 512-dimensional
`text-embedding-3-small` vectors while a private service-role PostgreSQL
function combines cosine similarity, full-text rank, importance, and
confidence after principal/status/expiry filters. Raw vectors never leave the
bridge/database boundary. `sphere_status` is a separate read-only tool that
returns only sanitized passive heartbeats, declared body capabilities, service
availability, and optional acknowledgement metadata; it cannot actively probe
physical hardware.

See [TOOLS.md](TOOLS.md) for the complete tool inventory, execution flow,
intent guards, limitations, and the deployment/background-audio decisions that
are currently tabled.

## Response contract

The two-character command set remains the semantic cross-component contract:

`GS`, `GP`, `GC`, `RS`, `RF`, `YP`, `BS`, `PS`, `PP`.

Any change must update firmware, bridge, Android, dashboard, protocol docs,
tests, and `SPEC.md` together.
