# Bridge Phase 2 HTTP Transport Boundary

Recorded: 2026-08-12
Refactor branch: `refactor/http-transport`
Stacked base: `refactor/bridge-contracts` at `dfbf397`

Phase 2 extracts HTTP concerns from the bridge composition and service facade
without changing the legacy `/v1` protocol. The remaining physical ESP check
from Phase 0 is still deferred and is not claimed by this phase.

## Extracted Boundary

`bridge/http_transport.py` now owns:

- exact and pattern route definitions
- pure route matching into endpoint names and validated path captures
- shared-token authorization
- header/fallback device-ID resolution through the canonical validator
- content-length handling and one JSON object reader
- common string-field validation
- endpoint-specific transport translation
- CORS, request IDs, JSON/audio serialization, redacted access events, and
  privileged-operation audit events

Route matching does not receive the bridge state and therefore cannot mutate
it. After a route and authorization decision, the handler invokes a separate
endpoint method against the injected `BridgeState` facade.

## Compatibility

`wearabllm_bridge.make_handler()` remains as a narrow compatibility adapter.
Existing tests, scripts, and the bridge entry point do not need import changes.
`optional_bool()` and `json_bytes()` are re-exported through their existing
module import path for the same reason.

The Phase 0 golden fixture file is unchanged. Existing CORS headers, HTTP
statuses, request-ID headers, audio responses, legacy error dictionaries, and
target-device checks are retained. The previously unbounded JSON endpoints
now share the parser but retain no new byte cap, avoiding a silent protocol
limit during this extraction.

Hosted deployment staging and the Docker image include `http_transport.py`.

## Verification

The Phase 2 candidate passed:

- 182 bridge tests on Python 3.12, including all Phase 0 golden HTTP contracts,
  the Phase 1 internal contracts, and six transport architecture tests
- 12 bench/helper tests
- Android bridge-protocol tests and TypeScript typecheck
- protocol consistency and Python/shell syntax checks
- isolated dry-run HTTP/audio/action/TTS smoke
- Hugging Face deployment dry-run including `http_transport.py`
- unchanged `bridge/contract_fixtures/v1/golden_shapes.json`
- `git diff --check`

Firmware source did not change, so a firmware build and ESP test remain
intentionally outside this phase.

## Phase 3 Handoff

The transport now calls typed query and interaction methods but still depends
on `BridgeState` as a broad facade. Phase 3 can introduce `BridgeService`, move
conversation/presence/action orchestration behind it, and narrow the object
injected into `make_handler()` while retaining the same route tables and
golden response gates.
