# WearabLLM Protocol Migration Roadmap

Status: Future work after the seven-phase bridge refactor
Created: 2026-08-12

## Purpose

Move active clients from the frozen `/v1` compatibility contract to the
normalized `/v2` envelope without interrupting the shared Sphere conversation,
physical delivery, or recovery paths. This is a rollout roadmap, not Phase 8 of
the completed bridge refactor.

## Starting Point

- The private bridge serves `/v1` and `/v2` from the same transport, service,
  policies, stores, and action queue.
- Android source is the first v2 client, but the installed APK is still the
  last verified v1 build.
- The local Web dashboard and Waveshare firmware remain on v1.
- The board's current firmware image passed a full physical regression on
  2026-08-12 and embeds `/v1/query` and `/v1/tts`.
- There is no v1 removal date. Privacy-safe production version-usage
  instrumentation now exists, but its observation window has not been
  collected.

## Workstream 1 — Migrate Web Deliberately

Keep the Web console's browser-facing `/api/*` contract stable while changing
its server-side bridge proxy from v1 to v2. Unwrap and validate the v2 envelope
at that proxy boundary so UI code does not need an all-at-once rewrite.

Deliverables:

1. Add Web proxy tests for success, typed errors, conversation/session routes,
   interaction creation, and action-status polling using the shared v2 fixtures.
2. Switch one read-only path first, preferably health or conversation fetch.
3. Switch text/interaction writes only after the read path is stable.
4. Verify refresh persistence, archive/rename, delivery-off chat, and delivery-
   on action flow through terminal `played`.
5. Keep a one-line endpoint-prefix rollback to v1 until the observation window
   closes.

Exit gate:

- Web is verified on v2 without changing its visible behavior.
- The v1 proxy path remains available as an immediate rollback.
- Android, Web, and a v1 Waveshare can share one conversation/action queue.

## Workstream 2 — Gate and Migrate Firmware Physically

Do not change the board merely because the v2 route exists. First reconnect the
currently flashed v1 board and repeat the known-good baseline. Only then prepare
a separate firmware change that understands the v2 query envelope while keeping
binary TTS behavior unchanged.

Required pre-migration baseline:

- flash/image freshness and embedded v1 URL checks
- boot, PSRAM, display, microphone, speaker, Wi-Fi, and TLS
- direct voice turn with non-silent capture, STT, reply, display, and TTS
- v1 action claim and terminal acknowledgement

Candidate migration:

1. Add bounded v2 envelope parsing with explicit typed-error handling.
2. Keep the previous known-good v1 binary and configuration available.
3. Flash over USB; do not combine the migration with OTA, sensor, audio, or UI
   feature work.
4. Repeat the full physical matrix, including long reply, persistence failure,
   unavailable action, reboot during polling, and terminal `played` proof.
5. Roll back immediately to the retained v1 image if parsing, memory use, TLS,
   display, or audio regresses.

Exit gate:

- The physical board completes the full v2 voice/action regression.
- A known-good v1 image remains recoverable.
- Firmware source, image hash, flashed port, and live bridge revision are
  recorded together.

## Workstream 3 — Gather `/v1` Usage Evidence

Implementation status: counters, client headers, durable aggregate migration,
and the protected snapshot endpoint are complete. Applying the migration,
deploying the tagged clients, and collecting the observation window remain.

Add privacy-safe protocol-version observability before discussing removal.
Route version is operational metadata; do not log tokens, transcripts, query
parameters, memory, Wi-Fi values, or raw device credentials.

Deliverables:

1. Add bounded counters for v1/v2 requests by route family, status class, and
   declared body/device category.
2. Distinguish known clients only with existing safe device IDs or a new
   non-secret client/version header; never infer identity from content.
3. Define an observation window that includes normal Android, Web, Waveshare,
   bench, and recovery use—not merely idle production days.
4. Document unexplained v1 traffic and resolve it to a client or retained
   operational script before changing compatibility policy.
5. Preserve the metric definitions and evidence snapshot with the eventual
   deprecation proposal.

Exit gate:

- Every known active client and recovery tool has an owned protocol version.
- The observation window shows no unexplained v1 traffic.
- Evidence can be reviewed without exposing user content or secrets.

## Workstream 4 — Consider Compatibility Cleanup

Cleanup is optional. Fewer branches are not worth losing a working recovery
path. Start a separate proposal only after Android, Web, firmware, and bench
tools are verified on v2 and Workstream 3 provides usage evidence.

Required decision gates:

1. Name the exact v1 routes or compatibility shims proposed for removal.
2. Identify the owner and rollback for every former caller.
3. Keep the prior bridge revision and known-good firmware image deployable.
4. Publish a deprecation notice and observation period before removal.
5. Run the complete origin/destination matrix and persistence-failure case on
   the removal candidate.
6. Remove v1 fixtures only in the same separately reviewed change that removes
   the corresponding supported behavior; never weaken tests in advance.

Possible outcomes:

- **Retain v1 indefinitely:** correct when recovery value exceeds maintenance
  cost.
- **Deprecate selected routes:** correct when evidence proves narrow routes are
  unused but firmware/recovery routes remain valuable.
- **Remove v1 completely:** allowed only when every client and rollback path is
  v2-native and the user explicitly approves the break.

## Cross-Cutting Rollback Contract

- Bridge rollback: redeploy the last dual-protocol revision.
- Android rollback: switch the endpoint prefix to v1 and reinstall in place.
- Web rollback: switch the server-side proxy prefix to v1; browser API remains
  unchanged.
- Firmware rollback: flash the retained v1 image over USB.
- Data rollback: none should be necessary because v1 and v2 share the same
  typed service contracts and persistence backends.

No workstream authorizes removing v1, extracting secrets for tests, claiming
physical playback without acknowledgement, or combining protocol migration
with unrelated product features.
