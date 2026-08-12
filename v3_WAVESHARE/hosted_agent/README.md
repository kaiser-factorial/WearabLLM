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

Private Docker Space for the shared Sphere backend. Waveshare, Android, and Web
console use one authenticated API and Supabase-backed conversation.

The current image uses direct OpenAI APIs for STT, assistant generation, and
TTS. It also provides body presence, conversation sessions/titles/archive,
agent configuration, compact and provenance-rich durable memory, hybrid vector
retrieval, built-in web search with durable citations, read-only Sphere status,
read-only build-time source inspection, visible tool activity, a persistent
cross-body expression queue, and bounded authenticated capability-driven sensor
and scheduling tools for the Ducati ESP32-S3 sensor body.

## Required Space secrets

- `OPENAI_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `WEARABLLM_DEVICE_TOKEN`

## Required Space variable

- `WEARABLLM_PRINCIPAL_ID`: stable owner identifier, such as `corina`

Never expose the device token or Supabase service role in client code. The
Space fails closed when required hosted credentials/backends are unavailable.

## Defaults

```text
WEARABLLM_PROVIDER=openai
WEARABLLM_STT=openai
WEARABLLM_STT_MODEL=gpt-4o-mini-transcribe
WEARABLLM_LLM_MODEL=gpt-5.4-mini
WEARABLLM_TTS_MODEL=gpt-4o-mini-tts
WEARABLLM_TTS_VOICE=marin
WEARABLLM_MEMORY_BACKEND=supabase
WEARABLLM_CONVERSATION_BACKEND=supabase
WEARABLLM_ACTION_BACKEND=supabase
WEARABLLM_WEB_SEARCH=1
WEARABLLM_MAX_TOOL_ROUNDS=4
```

Agent settings can override model, voice, system prompt, TTS instructions, and
output-token cap at runtime through the authenticated admin API and Supabase
`wearabllm_agent_settings` table.

## Deployment

From the repository root:

```bash
python3 v3_WAVESHARE/scripts/deploy_hf_space.py \
  --repo-id YOUR_HF_ACCOUNT/wearabllm-agent
```

The uploader selects runtime bridge files and builds a bounded source manifest
from explicitly approved first-party paths. It never reads or uploads firmware
config, captures, local `.env` files, dependency trees, or secrets.
