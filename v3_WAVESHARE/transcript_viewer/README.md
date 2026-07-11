# WearabLLM Conversation Console

Local-only multi-device console for the shared WearabLLM conversation.

```bash
./v3_WAVESHARE/scripts/run_transcript_viewer.sh
```

Opens `http://127.0.0.1:8787`.

## What it does

- Shows the **shared principal conversation** across device bodies
  (`wearabllm-esp32` home base, `web-console`, future `wearabllm-wearable`)
- Lets you **filter by body** and **reply from the browser**, continuing the
  same thread the Waveshare uses
- Keeps a secondary **device event feed** from private Supabase transcript rows
  (command + mic transcript log)
- Binds only to `127.0.0.1`; device tokens stay in the Python proxy and never
  reach browser JavaScript

## Configuration

Reads ignored firmware `sdkconfig`:

| Key | Use |
|---|---|
| `WEARABLLM_BRIDGE_URL` | Hosted or local bridge `/v1/query` URL |
| `WEARABLLM_BRIDGE_AUTH_TOKEN` | Device token for bridge APIs |
| `WEARABLLM_TRANSCRIPT_LOG_URL` | Optional Supabase transcript Edge Function |
| `WEARABLLM_TRANSCRIPT_DEVICE_TOKEN` | Optional transcript token |

Overrides:

```bash
python3 v3_WAVESHARE/transcript_viewer/server.py \
  --bridge-url https://your-space.hf.space/v1/query \
  --bridge-token "$WEARABLLM_DEVICE_TOKEN" \
  --no-open
```

## API surface (local proxy)

| Method | Path | Upstream |
|---|---|---|
| GET | `/api/bootstrap` | local |
| GET | `/api/conversation` | bridge `GET /v1/conversation` |
| GET | `/api/devices` | bridge `GET /v1/devices` |
| GET | `/api/sessions` | bridge `GET /v1/conversation/sessions` |
| POST | `/api/reply` | bridge `POST /v1/query_text` |
| POST | `/api/session/reset` | bridge `POST /v1/session/reset` |
| GET | `/api/transcripts` | Supabase transcript function |

## Hosted bridge requirement

Conversation reads need the bridge endpoints:

- `GET /v1/conversation`
- `GET /v1/devices`
- `GET /v1/conversation/sessions`

Redeploy the Hugging Face Space after merging those bridge changes so the
console can load live turns. Replies already work against the existing
`/v1/query_text` path once the token is configured.

## Multi-device model

All bodies share one principal conversation store. Each turn is tagged with
`device_id`:

- `wearabllm-esp32` — home Waveshare
- `web-console` — this UI
- `wearabllm-wearable` — reserved for the portable companion

When the wearable arrives, flash it with its own device id/token; the console
will list it automatically once it appears in turns.
