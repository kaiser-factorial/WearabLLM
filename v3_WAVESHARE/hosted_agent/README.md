---
title: WearabLLM Unified Agent
emoji: "✨"
colorFrom: purple
colorTo: blue
sdk: docker
app_port: 7860
suggested_hardware: cpu-basic
pinned: false
---

# WearabLLM Unified Agent

Protected HTTPS bridge for WearabLLM device bodies. Its Space source stays
private, while device traffic reaches the HTTPS endpoint and is protected by a
separate device token. It sends speech/text through OpenRouter, returns the
existing LED-command/reply contract, and stores conservative long-term memories
in the private Supabase `wearabllm_memories` table.

Recent conversation is also stored privately in Supabase. It is bounded by the
bridge's configured history-turn limit and is cleared by `POST /v1/session/reset`
without deleting durable memories.

After one hour without a turn, the next request consolidates the completed
session into a compact summary and conservative durable facts, archives every
raw turn privately, and starts fresh working context. The raw archive is never
included in prompts automatically and has no automatic expiry.

This Space accepts only device requests containing the rotatable
`X-WearabLLM-Device-Token` header. Never make its device token or Supabase
service-role key public.

## Required Space secrets

- `OPENROUTER_API_KEY`: dedicated OpenRouter key with a strict credit limit.
- `SUPABASE_URL`: existing Supabase project HTTPS URL.
- `SUPABASE_SERVICE_ROLE_KEY`: server-only Supabase service-role key.
- `WEARABLLM_DEVICE_TOKEN`: a new random device token, copied separately into
  each device's ignored local firmware configuration.

## Required Space variable

- `WEARABLLM_PRINCIPAL_ID`: stable owner identifier, for example `corina`.

The local disk is intentionally never used for durable memories. A failed
Supabase configuration prevents the Space from starting instead of silently
creating a second, disposable memory store.

The default models are configurable with Space variables:
`WEARABLLM_LLM_MODEL`, `WEARABLLM_STT_MODEL`, `WEARABLLM_TTS_MODEL`, and
`WEARABLLM_TTS_VOICE`. The Docker image defaults to OpenRouter model slugs that
support reply generation, WAV transcription, and WAV speech output respectively.
