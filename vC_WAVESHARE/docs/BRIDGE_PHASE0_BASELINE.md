# Bridge Phase 0 Baseline

Recorded: 2026-08-12
Refactor branch: `refactor/bridge-contract-baseline`

This is the rollback and comparison point for the bridge refactor. Later
phases must preserve its `/v1` client behavior unless a separately reviewed
protocol change explicitly says otherwise.

## Source Baseline

| Surface | Recorded revision/state |
|---|---|
| GitHub `main` | `561c3f06815ab85198baf85d647de9de5664d7ae` |
| Private Hugging Face Space | `9329dac05f1fc1448011f38db22f434659d7abcd` |
| Space visibility | private |
| Space last modified | `2026-08-12T10:54:39Z` |
| Supabase migration head | `20260812010000_expand_conversation_turn_content.sql` |
| Migration parity | 15 local / 15 remote, exact match |

The live `/health` check returned HTTP 200 with `ok: true` and reported:

- provider: OpenAI
- conversation, durable-memory, and action backends: Supabase
- conversation persistence: enabled
- household-memory retrieval: hybrid
- source tools and web search: enabled
- device authentication: required
- maximum audio body: 524,288 bytes
- maximum stored conversation-turn content: 65,536 characters
- maximum tool rounds: 8

The health payload deliberately does not expose Git, Space, database, token,
or credential identifiers. Deployment revision is verified out of band with
the authenticated Hugging Face API.

## Automated Baseline Before Phase 0 Changes

The clean rewritten repository passed 154 bridge tests on the bundled Python
3.12 runtime. The preceding cleanup also passed:

- 12 bench/helper tests
- Android TypeScript typecheck
- Android protocol tests
- protocol consistency validation
- isolated bridge preflight and dry-run HTTP/audio/action/TTS smoke
- fresh ESP-IDF firmware build and image gate
- Hugging Face deployment dry-run

## Phase 0 Candidate Verification

The completed Phase 0 candidate passed on 2026-08-12:

- 168 bridge tests, including route-family contracts, malformed and oversized
  input, persistence-failure behavior, request-ID correlation, and log redaction
- 12 bench/helper tests
- Android bridge-protocol tests and TypeScript typecheck
- protocol consistency and Python/shell syntax checks
- isolated dry-run HTTP/audio/action/TTS smoke using temporary conversation,
  memory, action-queue, configuration, and capture paths
- clean ESP-IDF build (1,290 steps) and firmware image verification; the app
  image is 1,277,536 bytes with 70% of the smallest app partition free
- Hugging Face deployment dry-run including the new observability module

The remaining exit gate is a regression against the deployed candidate from
the Web console and the Waveshare body. Android live testing remains deferred;
its automated protocol coverage passed.

## Live and Hardware Baseline

- Hosted `/health`: verified 2026-08-12
- Web shared-conversation and refresh behavior: previously verified 2026-08-12
- Waveshare voice/query/display/TTS/action loop: previously verified; a fresh
  Web and Waveshare regression is required for the Phase 0 candidate
- Android: retained protocol/typecheck evidence; user-requested live Android
  regression is deferred until the device is available

## Contract Evidence

- Human-readable route inventory: `BRIDGE_API_CONTRACT.md`
- Golden machine-readable shapes:
  `../bridge/contract_fixtures/v1/golden_shapes.json`
- Integration coverage: `../bridge/test_bridge_contracts.py`
- Privacy/redaction coverage: `../bridge/test_observability.py`

The fixtures intentionally capture the legacy mixture of response shapes.
Normalizing everything to a new envelope is deferred to a separately versioned
`/v2` project.
