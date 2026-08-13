# Bridge Protocol Usage Evidence

Status: Implemented; observation window not yet collected
Created: 2026-08-12

## Purpose

This instrumentation supports the v1-to-v2 migration decision without
collecting conversation or device data. It answers which protocol, coarse
route family, client release, and outcome class are active. It does not decide
that v1 should be removed.

## Client Identification

Clients send two non-secret headers:

```text
X-WearabLLM-Client: android
X-WearabLLM-Client-Version: 0.1.0
```

Accepted client names are a fixed vocabulary: `android`, `web-console`,
`waveshare`, `bench-smoke`, `bench-doctor`, and `preflight`. Missing,
unrecognized, or malformed names become `unknown`; malformed versions become
`unknown`. Versions are release-shaped numeric values with an optional bounded
suffix. These headers are self-declared operational labels, not authentication
or authorization. The bridge accepts the labels only on a request with valid
bridge authentication (or in local tokenless mode); public unauthenticated
health probes always aggregate as `unknown`.

## Stored Dimensions

The bridge accumulates daily counters by:

- protocol version (`v1` or `v2`)
- coarse route family such as `query_text`, `query_audio`, or `conversation`
- HTTP method
- status class (`2xx`, `4xx`, and so on)
- allowlisted client name and bounded release version

The schema has no raw path, path identifier, query string, request ID, device
ID, transcript, memory, body, payload size, token, credential, Wi-Fi value, or
other content field. Route families come from matched handler identities, so a
path UUID or device identifier cannot become a metric label.

## Persistence and Failure Behavior

Hosted mode uses `wearabllm_protocol_usage_daily` in Supabase. Requests only
increment an in-memory aggregate; a background worker flushes batches through
a service-role-only function. If Supabase is unavailable, counts remain pending
and the user request still completes normally. The flush function maintains a
rolling 90-day retention window. Local mode is memory-only.

The migration is
`supabase/migrations/20260812020000_add_protocol_usage_aggregates.sql`.

## Reading a Snapshot

Authenticated operators can read up to 90 days:

```text
GET /v1/admin/protocol-usage?days=30
GET /v2/admin/protocol-usage?days=30
```

The v1 route returns `{ok, usage}`. The v2 alias returns the same data inside
the standard `{ok: true, data: ...}` envelope. The snapshot merges durable and
not-yet-flushed counters and declares its privacy properties in the response.

## Evidence Rules

An observation window should include normal Android, Web, Waveshare, bench,
preflight, and recovery use. Public hosting health checks may legitimately
appear as `unknown` on the `health` route; they are not evidence of an unknown
product client. Any other unexplained v1 activity must be resolved before a
compatibility change is proposed. Preserve the metric definition and snapshot
with that proposal.
