# WearabLLM Supabase schema

This directory is the private durable substrate for the hosted WearabLLM agent.
Apply migrations only after creating a new Supabase project; no project URL or
secret belongs in this repository.

## First setup

```bash
supabase link --project-ref YOUR_PROJECT_REF
supabase db push
```

Set these placeholders as Hugging Face **Space Secrets**:

```text
SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
SUPABASE_SERVICE_ROLE_KEY=YOUR_SERVER_ONLY_SERVICE_ROLE_KEY
```

Set `WEARABLLM_PRINCIPAL_ID` as a non-secret Space variable.

Never place the service-role key in firmware, the Android app, browser
JavaScript, or a committed environment file.

## What is stored

- `wearabllm_conversation_turns` and `wearabllm_conversation_sessions`: recent
  shared context, with completed sessions archived separately.
- `wearabllm_memories`: the existing compact auto-extracted memory store.
- `wearabllm_memory_records`: the richer long-term layer for preferences,
  roomies, household facts, routines, relationships, and instructions. Records
  retain source, confidence, importance, confirmation, expiry, and supersession
  state so later corrections do not silently overwrite history.
- `wearabllm_device_actions`: persistent phone/dashboard-to-Waveshare actions
  with idempotency, a board claim lease, and delivery/playback state.
- `wearabllm_agent_settings`: live assistant model, voice, prompts, and TTS
  instructions used by dashboard and hosted bridge.

The action queue, shared conversation, archive, titles, compact memory, and
agent settings are implemented. The richer `wearabllm_memory_records` table is
still a schema foundation: model tools, retrieval, inferred-fact extraction,
review, correction, and deletion remain future work.

## Applied migrations

The current schema sequence is:

```text
20260623130000 device transcripts
20260710000000 compact agent memory
20260710010000 conversation turns
20260710020000 sessions and archive
20260809000000 cloud control plane and richer memory records
20260809010000 agent settings
20260809020000 device action status alignment
20260809030000 conversation titles
```

Confirm local and remote versions match before deploying bridge code:

```bash
supabase migration list
```
