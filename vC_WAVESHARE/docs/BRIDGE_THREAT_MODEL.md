# WearabLLM Bridge Threat Model

Status: Phase 0 baseline
Recorded: 2026-08-12

## Assets and Trust Boundaries

The bridge protects:

- the shared private conversation and household memory
- OpenAI and Supabase credentials
- Wi-Fi credentials and ignored firmware configuration
- device-targeted physical actions and their true delivery state
- dashboard-editable prompts and model/TTS configuration
- allowlisted private source bundles
- audit metadata describing privileged operations

Untrusted inputs cross the boundary through HTTP bodies/headers, model output,
tool arguments, provider responses, Supabase rows, and device acknowledgements.
The browser, Android app, Waveshare, hosted Space, providers, and database do
not become mutually trusted merely because they participate in one Sphere.

## Threats and Current Controls

| Surface | Principal threats | Phase 0 and existing controls | Deferred hardening |
|---|---|---|---|
| HTTP authentication | Missing/guessed token, timing leakage, credentials in logs | Token required in hosted mode; constant-time comparison; token/header never logged | Per-device credentials and rotation |
| Device identity | One body claims or acknowledges another body's work | Strict device-ID grammar; path target must equal header identity for claim, manifest, and ack | Explicit policy principal object in Phase 4 |
| Model output and tools | Malformed/adversarial output, unbounded loops, unauthorized mutation | Parser fallback; nine-command allowlist; maximum eight rounds; per-turn web gating; memory confirmation rules | Typed model states and policy separation |
| Conversation persistence | Partial user-only turn, oversized content, false success | Atomic exchange write; 65,536-character database contract; usable reply plus explicit failed persistence state | Typed persistence result in Phase 1 |
| Durable/household memory | Secret storage, false inference, wrong-principal access, content leakage | Secret/contact screening, staged confirmation for sensitive facts, principal-scoped stores/RPCs, bounded public summaries | Privileged policy and durable audit event in Phase 4 |
| Source inspection | Traversal, arbitrary file reads, secret or giant-file disclosure | Deployment-time allowlist, path validation, bounded chunks and bundle size, read-only tools | Typed tool results in Phase 5 |
| Device actions | Target mismatch, replay, false `played`, forged sensor result | UUID/idempotency validation, target checks, leases/expiry, status machine, sensor result validation, terminal acknowledgement required | Per-device auth and persistent audit correlation |
| Admin config | Prompt/model mutation, invalid values, backend-error leakage | Token, field allowlist and length validation, public config projection, bounded unexpected errors, audit metadata | Dedicated policy decision and persistent audit store |
| API-key mutation | Key disclosure, invalid key, unsafe storage, backend-error leakage | Token, validation before replacement, macOS Keychain storage, key never logged/returned, bounded errors, audit metadata | Narrow provider/key adapter; hosted endpoint policy |
| Device Wi-Fi/config | Password/SSID leakage, shell injection, unsafe hardware values, unauthorized mutation | Runtime opt-in, token, explicit field validation, subprocess argument list, credentials passed by environment, password never returned/logged, audit metadata | Redacted preview plus policy/executor split |
| Logs and status | Transcript/reply/memory/SSID/token leakage | Default allowlisted JSON metadata only; raw exception messages omitted; server-generated request IDs; health remains credential-free | Persistent audit sink and retention controls |

## Logging Privacy Contract

Default runtime events may contain only:

- timestamp, severity, stable event name, and request ID
- HTTP method, route path without query string, status, duration, and byte counts
- validated device ID
- stable operation/outcome/error codes and exception class
- provider/backend/model identifiers and bounded counters

They must never contain request or response bodies, transcripts, replies, TTS
text, prompts, memory contents, source excerpts, SSIDs, BSSIDs, passwords,
tokens, API keys, authorization headers, raw provider/database responses, or
raw exception messages.

`--debug-content-logs` is an explicit local diagnostic escape hatch for query
and TTS content. The process refuses to start with this flag when
`WEARABLLM_HOSTED=1`. It never enables credential or Wi-Fi logging.

## Audit-History Decision

The user approved persistent audit history. Phase 0 emits a redacted audit
event stream for admin config changes, API-key changes, device configuration,
and action acknowledgement. It stays in structured process logs during the
contract-freeze phase so Phase 0 does not introduce a new database mutation or
migration.

Phase 4 will add a service-role-only Supabase audit adapter with these defaults:

- allowlisted metadata columns rather than arbitrary JSON request bodies
- principal, validated body ID, request ID, operation, outcome, stable error
  code, and timestamps
- no transcript, reply, memory content, source excerpt, SSID, password, token,
  API key, or raw exception field
- append-only writes from the bridge service role
- 30-day default retention, adjustable before migration review
- audit write failure cannot authorize, execute, or falsely report a mutation;
  the mutation policy must define whether each high-risk operation fails closed

The schema and retention job require a separately reviewed backward-compatible
migration. They are intentionally not smuggled into Phase 0.

## Required Negative Tests

Phase 0 tests prove:

- missing auth and invalid device IDs fail
- target mismatch prevents action claim/ack and sensor manifest registration
- malformed/non-object and oversized representative bodies fail predictably
- request IDs correlate response headers with exactly one completion event
- transcript, reply, TTS text, admin instructions, API key, SSID, password, and
  exception messages do not appear in default logs
- privileged operations produce audit events without private values
- unexpected provider/runtime errors return bounded client messages
