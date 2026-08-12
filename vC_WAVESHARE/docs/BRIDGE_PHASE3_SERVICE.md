# Bridge Phase 3 Service Boundary

Recorded: 2026-08-12
Refactor branch: `refactor/bridge-service`
Stacked base: `refactor/http-transport` at `f16aff1`

Phase 3 introduces an application service between the HTTP transport and the
bridge's provider/storage composition. It preserves `BridgeState` as a
compatibility facade and does not change the legacy `/v1` protocol.

## Service Responsibilities

`bridge/bridge_service.py` now owns:

- typed text-query orchestration
- audio-query orchestration across injected WAV saving, inspection, STT, and
  capture-recording adapters
- interaction creation and target-body response normalization
- action listing, lookup, claim, target authorization, acknowledgement, and
  completed sensor-loop publication
- device presence using an injected monotonic clock and UTC timestamp provider
- sensor-manifest registration and catalog assembly
- conversation reset, archive, rename, expiry/consolidation, and clearing
- local and persisted conversation assembly through one typed
  `ConversationView` and `ConversationTurnView`

Expected application failures have explicit service exception types for
validation, not-found, unavailable, and permission outcomes. Unexpected store
or provider failures continue to propagate to the existing bounded transport
error handling, except the deliberately best-effort sensor persistence path.

## Composition and Compatibility

`BridgeState` constructs `BridgeService` with explicit dependencies:

- assistant-generation gateway
- action and conversation stores
- history provider, clearer, and lock
- text normalization
- device catalogs and infrastructure-body exclusions
- exception sink
- monotonic clock and presence TTL
- presence and sensor registries/locks
- WAV saver/inspector, transcriber, and capture recorder

Existing `BridgeState` methods remain as narrow delegates so focused tests,
scripts, model tools, and the Phase 2 transport do not need coordinated import
changes. The transport no longer reaches directly into action or conversation
stores.

Hosted deployment staging and the Docker image include `bridge_service.py`.

## Verification

The Phase 3 candidate passed:

- 188 bridge tests on Python 3.12, including all frozen Phase 0 HTTP contracts,
  Phase 1 internal contracts, Phase 2 transport tests, and six direct
  server-free service tests
- direct proof that local and Supabase-style conversation paths return the
  same internal view and normalized turn shape
- 12 bench/helper tests
- Android bridge-protocol tests and TypeScript typecheck
- protocol consistency and Python/shell syntax checks
- isolated dry-run HTTP/audio/action/TTS smoke
- Hugging Face deployment dry-run including `bridge_service.py`
- unchanged `bridge/contract_fixtures/v1/golden_shapes.json`
- `git diff --check`

Firmware and ESP behavior did not change. Their deferred Phase 0 physical test
and a live hosted deployment are not claimed here.

## Phase 4 Handoff

Privileged operations still execute through existing `BridgeState` methods and
stores. Phase 4 can add explicit policy decisions and narrow mutation services
for admin configuration, API-key replacement, device Wi-Fi changes, memory
mutation, source inspection, and target-body access without reopening HTTP
routing or query/conversation orchestration.
