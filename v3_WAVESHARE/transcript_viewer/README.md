# WearabLLM Conversation Console

Local-only multi-device console for the hosted Sphere conversation.

```bash
./v3_WAVESHARE/scripts/run_transcript_viewer.sh
```

Opens `http://127.0.0.1:8787`.

## What it does

- Shows the **shared principal conversation** across device bodies
  (`wearabllm-esp32` Waveshare, `wearabllm-android`, `web-console`, future `wearabllm-wearable`)
- Shows live body presence horizontally across the top
- Lets you reply from the browser, continuing the same shared thread
- Receives targeted Sphere expressions as a semantic color/text treatment;
  optional browser speech is disabled by default
- Shows durable web-search sources under assistant turns
- Keeps conversation history in the side panel. `+` ends and preserves the
  current nonempty conversation in the normal list, then starts a new one;
  repeated clicks on an empty conversation do not create empty history items.
- Renders lightweight Sphere Markdown as safe headings, lists, links, emphasis,
  and code while preserving the same turn as plain text for Waveshare/TTS.
- Downloads any current or archived conversation as standalone HTML, structured
  JSON, or plain UTF-8 text. Exports use the client-visible API and never include
  private model tool context.
- Keeps the conversation in a centered, bounded reading lane at extreme browser
  zoom-out so left/right message alignment remains visually connected.
- Moves archived conversations behind a compact bottom Archive control
- Only the explicit Archive action moves a conversation behind that control;
  starting a new conversation no longer archives or clears the previous one.
- Places Rename and Archive behind each conversation's `...` menu
- Keeps a secondary **device event feed** from private Supabase transcript rows
  (command + mic transcript log)
- **Command center** tab for live agent personality:
  system prompt, live OpenAI LLM/TTS model choices, TTS voice + delivery
  instructions
- **Deploy to Hugging Face** from the laptop (code upload only; secrets stay local)
- **Sensor** tab for direct Web Bluetooth readings from the standalone Ducati
  ESP32-S3 v6.2 temperature sensor; readings remain browser-local
- Binds only to `127.0.0.1`; device tokens stay in the Python proxy and never
  reach browser JavaScript

## Temperature sensor tab

Open the dashboard in a Web Bluetooth-capable browser, select **Sensor**, and
choose **Connect sensor**. The page connects directly to the BLE service
advertised by `ducati_relay/v6.2_temperature_sensor`, v6.3, or the v6.4 sensor hub.
Use **Take reading** in the temperature card or press the physical sensor button
for each direct BLE reading. The page writes command byte `0x01` to the command
characteristic, and the resulting measurement arrives through the same versioned
notification packet used by physical button presses.

With v6.4, Sphere can discover the registered sensors, queue fresh readings, or
create a bounded recurring schedule
through the private hosted bridge. The sensor polls that bridge over authenticated
outbound HTTPS and posts structured results; confirmed Wi-Fi readings are folded
into the same Sensor history. No inbound LAN port is opened. The v6.4 Wi-Fi
credentials, device token, and TLS root CA live only in its Git-ignored local
configuration header.

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
| POST | `/api/sessions/<id>/rename` | bridge session rename |
| POST | `/api/sessions/<id>/archive` | bridge session archive |
| POST | `/api/reply` | bridge `POST /v1/query_text` |
| GET | `/api/interactions[/<id>]` | bridge action queue status |
| GET | `/api/body-actions/next` | claim the next `web-console` expression |
| POST | `/api/body-actions/<id>/ack` | report browser rendering/playback state |
| POST | `/api/session/reset` | bridge `POST /v1/session/reset` |
| GET/POST | `/api/admin/config` | bridge `GET/POST /v1/admin/config` |
| GET | `/api/admin/catalog` | bridge live OpenAI model catalog |
| POST | `/api/admin/api-key` | bridge OpenAI key validation + macOS Keychain update |
| POST | `/api/admin/deploy` | local `scripts/deploy_hf_space.py` |
| GET | `/api/transcripts` | Supabase transcript function |

### Command center notes

- **Save to agent** updates the live hosted bridge personality immediately.
- **Refresh live models** calls OpenAI through the bridge and limits the LLM and
  TTS pickers to models available to the configured account. Built-in TTS voice
  choices follow the selected model's supported set.
- **Save key & refresh** is only available on the local macOS bridge. The typed
  key is posted over localhost, validated before activation, stored in macOS
  Keychain, and never returned to or retained by the dashboard. For the hosted
  bridge, set the key in Hugging Face Space Secrets instead.
- When the bridge has Supabase service credentials, settings persist in
  `wearabllm_agent_settings` (apply migration
  `supabase/migrations/20260809010000_create_wearabllm_agent_settings.sql`).
- **Deploy to HF** only packages bridge source (`agent_config.py`,
  `durable_memory.py`, `wearabllm_bridge.py`, Dockerfile). It does not upload
  `sdkconfig` or API keys.
- Firmware device config (Wi-Fi, PTT, etc.) still uses the existing next-flash
  path; Sphere is the conversation + agent brain control surface first.

## Hosted bridge

Conversation reads need the bridge endpoints:

- `GET /v1/conversation`
- `GET /v1/devices`
- `GET /v1/conversation/sessions`

The deployed Space already exposes these endpoints. Redeploy after bridge
changes so dashboard and hosted API revisions do not drift.

## Multi-device model

All bodies share one principal conversation store. Each turn is tagged with
`device_id`:

- `wearabllm-esp32` — Waveshare
- `wearabllm-android` — Android phone app
- `web-console` — this UI
- `wearabllm-wearable` — reserved for the portable companion

When the wearable arrives, flash it with its own device id/token; the console
will list it automatically once it appears in turns.

Enable **Also play on Waveshare** to create a targeted interaction in addition
to the shared web reply. The board pulls that action from the hosted Supabase
queue and acknowledges playback; the composer updates from the same action
record.
