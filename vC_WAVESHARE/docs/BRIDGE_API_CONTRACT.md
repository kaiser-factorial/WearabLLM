# WearabLLM Bridge `/v1` Contract Inventory

Status: Phase 0 compatibility baseline
Recorded: 2026-08-12

This inventory describes the current HTTP edge before transport extraction.
It is descriptive, not a proposal. Exact representative shapes are frozen by
`bridge/contract_fixtures/v1/golden_shapes.json` and exercised by
`bridge/test_bridge_contracts.py`.

## Shared Transport Rules

- `/health` and `OPTIONS` are public. Every `/v1` GET and POST is protected
  when `WEARABLLM_DEVICE_TOKEN` is configured.
- The token is supplied as `X-WearabLLM-Device-Token` and compared with
  `hmac.compare_digest`.
- `X-WearabLLM-Device-Id` accepts only `[A-Za-z0-9._-]{1,80}`. If absent, the
  configured fallback device ID is used.
- JSON is ASCII-encoded and returned as `application/json`. TTS returns
  `audio/wav`.
- Every response now includes a server-generated `X-Request-Id`; response
  bodies are unchanged. Browsers may read the header through
  `Access-Control-Expose-Headers`.
- CORS remains `Access-Control-Allow-Origin: *` with the existing allowed
  methods and request headers. The device token remains the authorization
  boundary; CORS is not authorization.
- The common legacy error is `{"error": "..."}`. Session reset and device
  configuration also use legacy `{"ok": false, "error": "..."}` forms.
- Unexpected runtime messages are bounded before returning to clients. Raw
  provider, storage, subprocess, and Keychain exception text is not public.

## GET and Preflight Routes

| Method and path | Caller(s) | Auth / target rule | Input bound | Success shape | Relevant failures |
|---|---|---|---|---|---|
| `GET /health` | Web, Android, scripts, operations | Public | None | `{ok, service, config}` | Runtime/server availability |
| `GET /v1/admin/config` | Web dashboard | Token | None | `{ok, config}` | 401 |
| `GET /v1/admin/catalog` | Web dashboard | Token | None | `{ok, catalog}` | 401, 502 bounded provider failure |
| `GET /v1/interactions` | Web dashboard | Token | `target_device_id`; decimal `limit`, default 100 | `{ok, actions}` | 400 invalid limit/target, 401 |
| `GET /v1/interactions/{action_uuid}` | Web and Android status views | Token | UUID path | `{ok, action}` | 400 invalid action ID, 401, 404 |
| `GET /v1/sensors` | Web dashboard and model-support tooling | Token | optional `device_id` | `{ok, devices}` | 400 invalid device ID, 401 |
| `GET /v1/devices/{device_id}/actions` | Waveshare polling; diagnostic clients | Token; header device ID must equal path target | Device ID path | `{ok, action}`; action may be `null` | 400, 401, 403 target mismatch |
| `GET /v1/conversation` | Web and Android | Token | optional device/session; decimal `limit`, default 200, service clamp 1..500 | Conversation snapshot object | 400 invalid limit, 401, 500 bounded storage failure |
| `GET /v1/devices` | Web | Token | None | `{ok, devices}` | 401 |
| `GET /v1/conversation/sessions` | Web and Android | Token | None | `{ok, active_session_id, sessions}` | 401 |
| `OPTIONS *` | Browser preflight | Public | None | 204 empty body plus CORS/request-ID headers | None |

## POST Routes

| Path | Caller(s) | Auth / target rule | Body limit | Success shape | Relevant failures |
|---|---|---|---|---|---|
| `/v1/query` | Waveshare firmware, smoke script | Token; source device header validated | Runtime `max_audio_bytes`; live value 524,288 | Query result with command, reply, transcript, WAV metadata, tools, sources, persistence | 400 missing/invalid audio/device, 413 oversized, bounded 500 query result |
| `/v1/query_text` | Web and Android | Token; source and optional response device validated | No explicit Phase 0 byte cap | Same query result | 400 malformed/non-object JSON, missing transcript, invalid device; bounded 500 |
| `/v1/tts` | Waveshare firmware | Token | No explicit Phase 0 byte cap | `audio/wav` | 400 malformed/non-object JSON or missing text; bounded JSON 500 |
| `/v1/heartbeat` | Web and Android | Token; source device validated | Body unused | `{ok, device_id}` | 400, 401 |
| `/v1/devices/{device_id}/sensor-manifest` | Sensor/Waveshare firmware | Token; header device ID must equal path | 16,384 bytes | `{ok, manifest}` | 400 invalid body/manifest, 401, 403 mismatch |
| `/v1/session/reset` | Web and Android | Token | Body unused | Legacy reset/session object | 401, bounded 500 |
| `/v1/conversation/sessions/{uuid}/archive` | Web and Android | Token | Body unused | Legacy archive/session object | 400, 401, 404, bounded 500 |
| `/v1/conversation/sessions/{uuid}/rename` | Web and Android | Token | 2,048 bytes | `{ok, session}` | 400 invalid body/title, 401, 404, bounded 500 |
| `/v1/device_wifi` | Android/local administration | Token plus runtime opt-in `allow_device_config` | No explicit Phase 0 byte cap | Legacy config result including `password_set`; authorized response retains SSID for compatibility | 400, 401, 403 disabled, bounded 500 |
| `/v1/interactions` | Web and Android | Token | 16,384 bytes | `{ok, ...query, action, action_created}` | 400 invalid/missing origin/target/transcript, 401, bounded 500 |
| `/v1/admin/config` | Web dashboard | Token | 64,000 bytes | `{ok, config}` | 400 invalid patch, 401, bounded 500 |
| `/v1/admin/api-key` | Local Web administration | Token | 2,048 bytes | `{ok, key_storage, catalog}` | 400 invalid key/provider, 401, bounded 502 |
| `/v1/devices/{device_id}/actions/{uuid}/ack` | Waveshare firmware; diagnostic clients | Token; header device ID must equal path target | 2,048 bytes | `{ok, action}` | 400 invalid body/status/result, 401, 403 mismatch, 404 |

## Stable Query Shape

Text and audio queries return these top-level fields:

```text
command, reply, transcript, audio_bytes, saved_wav, wav_info,
sources, tool_results, persistence
```

`persistence` contains `status`, `backend`, `session_id`, and `message`.
Persistence failure is explicit while the generated reply remains usable.

## Stable Action Semantics

- Expression commands remain exactly `GS`, `GP`, `GC`, `RS`, `RF`, `YP`,
  `BS`, `PS`, and `PP`.
- Claim and acknowledgement are device-targeted.
- `queued`, `dispatched`, `delivered`, `rendered`, `tts_started`, `completed`,
  `played`, `failed`, and `expired` remain distinct.
- The bridge never upgrades an action to `played` without a matching body
  acknowledgement.

## Known Phase 0 Debt, Preserved Deliberately

- `/v1/query_text`, `/v1/tts`, and `/v1/device_wifi` now share the Phase 2 JSON
  reader but retain no explicit byte limit. Any newly introduced limit still
  requires compatibility evidence.
- Error envelopes are inconsistent. They remain frozen under `/v1`.
- The shared device token authenticates the principal but is not yet a
  per-device credential. Target-device equality remains a second boundary.
