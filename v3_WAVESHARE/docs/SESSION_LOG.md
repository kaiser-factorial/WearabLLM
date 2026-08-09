# WearabLLM Session Log

## 2026-07-10 — Shared conversation continuity

### Goal

Make the deployed unified agent retain a bounded recent conversation across
Hugging Face Space restarts and across device bodies. The Waveshare base and a
future wearable should therefore draw on the same current thread as well as the
existing shared durable memories.

### Starting State

- The protected Hugging Face Space at
  `brick-factorial/wearabllm-agent` is live and requires a device token.
- Supabase already stores conservative long-term facts in the private
  `wearabllm_memories` table.
- The bridge's recent conversation list is currently process-local. It is lost
  whenever the Space restarts or sleeps, and it has no source-device metadata.
- The Waveshare is disconnected; this work must remain fully testable without
  flashing or attaching it.

### Plan

1. Add a private Supabase `wearabllm_conversation_turns` table, scoped to the
   shared principal and recording device ID, role, content, and timestamp.
   Apply RLS with no anon/authenticated client policies; only the hosted bridge
   service role may access it.
2. Add a Supabase conversation-store adapter to the bridge. Before an LLM call,
   it loads the newest bounded turn window; after a successful reply, it saves
   the user and assistant turns. Durable-memory extraction remains separate and
   conservative.
3. Carry an `X-WearabLLM-Device-Id` header from the firmware into the bridge,
   using `wearabllm-esp32` as the staged base-device identifier. The future
   wearable will receive its own ID and credential in the next enrollment step.
4. Make `POST /v1/session/reset` remove the shared bounded conversation window
   for the principal. It must not delete durable memories.
5. Add unit coverage for turn ordering, bounded retrieval, persisted reload,
   reset behavior, and device-auth/device-ID handling. Rebuild the firmware and
   redeploy the protected Space.
6. Verify the deployed API with authenticated text requests, including a
   restart-safe follow-up and a reset. Do not flash the disconnected board.

### Guardrails

- Keep recent turns and durable memories private; neither belongs in client
  storage, firmware binaries, Space source, or logs.
- Keep the table bounded in use (default: 20 user/assistant turns) to control
  prompt size and cost.
- The current shared device token is transitional. The next phase will replace
  it with individually revocable per-device tokens before a wearable is added.
- A reset affects shared recent conversation only; it never erases durable facts
  without an explicit memory-administration action.

### Execution Record

- Implemented the `SupabaseConversationStore`, a bounded shared-turn adapter,
  device-ID validation, and a remote-aware session reset.
- Added `wearabllm_conversation_turns` through migration
  `20260710010000_create_wearabllm_conversation_turns.sql`, then applied it to
  the live Supabase project.
- Updated the protected Space to use the Supabase conversation backend and
  staged the Waveshare as `wearabllm-esp32`.
- Added coverage for shared-history reload, persistence, reset, turn ordering,
  device IDs, and invalid-device rejection. The bridge suite has 58 passing
  tests; the firmware image builds with the new header path.
- Rotated the hosted device token and replaced the local ignored firmware value
  after an earlier development command displayed the previous value. No source
  file or firmware image was published with it.
- The Space health and authenticated reset paths work. The hosted bridge is
  now being migrated from direct OpenAI credentials to OpenRouter for LLM, STT,
  and TTS. Add an `OPENROUTER_API_KEY` Space secret, then rerun the two-turn
  verification.

### OpenRouter Verification

- Added the `OPENROUTER_API_KEY` Space secret, deployed the provider migration,
  and confirmed that the Space reports `provider=openrouter`,
  `stt=openrouter`, and persisted Supabase conversation enabled.
- An authenticated cloud-only first turn requested the phrase `violet
  lighthouse`; the following turn recalled it correctly from the shared recent
  conversation. A final authenticated reset succeeded.
- The obsolete invalid `OPENAI_API_KEY` Space secret was removed after this
  successful verification. The hosted path now uses OpenRouter for LLM, STT,
  TTS, and memory extraction.

## 2026-07-10 — Session archive and one-hour consolidation

### Goal

Preserve full raw conversations privately for later review without allowing old
transcripts to overwhelm the unified agent's working context.

### Plan

1. Treat one hour without a turn as the end of an active shared session,
   regardless of whether the preceding turn came from the Waveshare or a future
   wearable.
2. Keep only the newest bounded turns from the active session in prompt context.
   Never load archived transcripts automatically.
3. On the first turn after an hour of inactivity, summarize the previous
   session, extract conservative durable facts from that summary, copy every raw
   turn to a private archive table, and begin a new active session.
4. Retain archived raw turns indefinitely by default. Session metadata and
   summaries make later search/export possible without retaining them in model
   context.
5. Make explicit session reset archive the active raw turns and start fresh; it
   does not delete durable memories or raw archives.
6. Add migration, unit coverage, deploy the protected Space, and verify the
   data lifecycle without requiring the disconnected board.

### Guardrails

- Archive rows retain the original turn ID and are private to the Supabase
  service role; this makes retrying archival safe and avoids client access.
- Consolidation is lazy on the next interaction, avoiding a background worker
  and unnecessary model calls while the system is idle.
- The one-hour boundary and archival retention are configuration values, so they
  can be changed later without changing the device protocol.

### Execution Record

- Added active session metadata and the private raw archive through migration
  `20260710020000_add_conversation_sessions_and_archive.sql`, then applied it
  to the live Supabase project.
- The hosted bridge now loads only the bounded active-session window. After one
  idle hour, it summarizes the old session, extracts conservative facts from
  that summary, archives every raw turn, and starts fresh context.
- Verified the live lifecycle with disposable cloud turns: a simulated
  two-hour gap archived two completed-session turns; the new active session
  held two turns; explicit reset archived those two and left zero active rows.
- Raw archive retention has no automatic expiry. Archived turns are not prompt
  context unless a future explicit search/retrieval feature requests them.
- The bridge suite now has 61 passing tests, including active-session rollover
  and archive ordering/lifecycle coverage.

## 2026-07-10 — Waveshare network transition

### Execution Record

- Rebuilt and flashed the Waveshare image with the new local Wi-Fi credentials.
- Captured a clean boot from the physical board: the ES7210 microphone, ES8311
  speaker driver, `Hi ESP` wake-word model, and hosted-agent URL initialized.
- The board joined the configured Wi-Fi network and received a private LAN address.
- The remaining physical validation is a spoken request, covering wake/PTT,
  microphone capture, hosted OpenRouter request, LED command, and audible TTS.
