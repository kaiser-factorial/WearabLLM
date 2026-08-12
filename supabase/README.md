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
- `wearabllm_device_actions`: device-neutral expressions targeted to any active
  body, with per-target idempotency, expiry, a claim lease, and terminal state.
- `wearabllm_agent_settings`: live assistant model, voice, prompts, and TTS
  instructions used by dashboard and hosted bridge.

The richer records are now available to Sphere through search, safe durable
remembering, sensitive yes/no confirmation, and explicit correct/forget tools.
Broad transcript extraction and a user review UI remain out of scope.
Conversation-turn metadata stores public
web citations and redacted tool audit results without putting them in spoken
reply text.

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
20260810000000 normalized Sphere expression actions
20260810010000 conversation citation and tool metadata
20260810020000 atomic explicit memory correction
20260810030000 pgvector columns and principal-scoped hybrid memory search
```

Confirm local and remote versions match before deploying bridge code:

```bash
supabase migration list
```
