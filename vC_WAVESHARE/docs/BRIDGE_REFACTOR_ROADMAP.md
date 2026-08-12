# WearabLLM Bridge Refactor Roadmap

Status: Phase 0 candidate verified; deployed Web/Waveshare regression pending
Created: 2026-08-12  
Scope: `vC_WAVESHARE/bridge/wearabllm_bridge.py` and its client-facing contracts

## Decision Summary

Refactor the bridge incrementally behind the existing `/v1` behavior. The
first work is contract capture and observability, followed by typed internal
results, HTTP extraction, service extraction, policy and privileged-action
isolation, model/tool pipeline cleanup, and configuration/adapters. A normalized
response envelope is a later protocol-versioning project, not an early cleanup.

The refactor must preserve the working shared-Sphere system:

```text
Waveshare / Android / Web
            |
            v
private authenticated bridge
            |
            +-- OpenAI model, STT, TTS, and bounded tools
            +-- Supabase conversations, memory, config, presence, and actions
            +-- device-targeted action acknowledgement
```

## Why Now

`wearabllm_bridge.py` is 2,901 lines. Its largest seams currently combine:

- HTTP routing, authorization, request parsing, and response serialization
- conversation orchestration, persistence, session management, and presence
- model prompting, model-output parsing, tool policy, execution, and summaries
- STT/TTS provider behavior and audio normalization
- admin configuration, API-key replacement, and device Wi-Fi mutation
- CLI/environment parsing and process startup

The code is functioning and has strong safety boundaries, but those boundaries
are difficult to audit because they share one module and several large methods.
The refactor is intended to make those boundaries explicit without changing
the product while they move.

## Protected Baseline

Before the first extraction branch:

1. Merge or deliberately choose the base containing draft PR #6 and commit
   `4c3c829` so the deployed persistence fix is not lost.
2. Branch from that exact reviewed base; do not refactor from an older `main`.
3. Record the live Space revision and the Supabase migration head.
4. Preserve the current verification baseline: 154 bridge tests, 12 bench
   helper tests, Android typecheck and protocol tests, protocol consistency,
   isolated bridge smoke, and hosted `/health`.
5. Capture representative responses before moving code.

The following behavior is non-negotiable throughout the refactor:

- `/v1` paths, HTTP statuses, fields, and legacy error bodies remain compatible.
- Unknown extra response fields remain safe for firmware to ignore.
- The nine two-character expression commands retain their meanings.
- Device tokens are checked with constant-time comparison.
- A body may claim or acknowledge only actions targeted to its device ID.
- `queued`, `dispatched`, `rendered`, `tts_started`, `completed`, `played`,
  `failed`, and `expired` remain distinct; the bridge never invents `played`.
- User/assistant persistence remains atomic, and a persistence failure remains
  explicit while the generated reply is still usable.
- Memory and source-tool results exposed to clients remain redacted and bounded.
- Tool rounds remain bounded, source access remains read-only/allowlisted, and
  web search remains gated by current-information intent.
- `/health`, logs, errors, and audit events never expose tokens, API keys,
  passwords, private memory content, or raw authorization headers.
- Existing local and Supabase modes remain supported until deliberately
  deprecated with evidence that no active workflow needs them.

## Target Boundaries

The target is a small composition root around explicit modules. The names below
are directional; behavior and dependency direction matter more than exact file
names.

```text
wearabllm_bridge.py        process startup and compatibility facade
        |
        +-- bridge_config.py       validated runtime configuration
        +-- http_transport.py      HTTP, auth boundary, parsing, serialization
        +-- bridge_service.py      conversational and action orchestration
        +-- bridge_policy.py       allow/deny and sensitive-operation decisions
        +-- bridge_contracts.py    typed internal request/result objects
        +-- model_protocol.py      model reply parsing and normalization
        +-- device_config.py       privileged config validation/preview/execution
        +-- audit.py               redacted structured events and request IDs
        |
        +-- existing adapters
            action_queue.py
            agent_config.py
            durable_memory.py
            household_memory.py
            source_code.py
            sphere_tools.py
```

Dependency direction:

- transport may call service and policy; service must not know about HTTP
- service may call adapter interfaces; adapters must not call transport
- policy decides whether an operation is allowed; executors perform it
- contracts may be imported by all layers but contain no runtime integrations
- audit receives sanitized events and must not become a second business-logic path

Do not split every existing module merely to make the tree look symmetrical.
`action_queue.py`, `durable_memory.py`, and the other current modules already
provide useful adapter seams. Extract only where a boundary becomes clearer or
a focused test becomes possible.

## Schema Choice

Start with standard-library `dataclass`, `Enum`, `TypedDict`, and explicit
validation functions. The hosted bridge currently has a deliberately small
runtime dependency set, so adding Pydantic is not justified for the first
refactor stages.

Initial typed internal contracts should cover:

- normalized model reply: command, reply, sources, and tool activity
- persistence result and stable error code
- text/audio query inputs and interaction requests
- action queue request, public action view, and acknowledgement
- conversation turn/session view models
- admin config changes and device-config preview
- structured API error and redacted audit event

Every internal contract needs one explicit adapter to the existing `/v1` dict.
Typing alone is not runtime validation; untrusted HTTP and model data must still
pass validation at their respective boundaries.

## Delivery Sequence

Each phase should be one small PR where practical. A phase may be split further,
but unrelated product features should not ride along.

### Phase 0 — Freeze Contracts and Add Observability

Suggested branch: `refactor/bridge-contract-baseline`

Deliverables:

- inventory every current route, auth requirement, request limit, success
  shape, failure shape, and known caller
- add golden fixtures/contract tests for `/health`, text/audio queries, TTS,
  interactions, action claim/ack, conversations, sensors, and admin endpoints
- add integration coverage for malformed bodies, missing auth, invalid device
  IDs, device-target mismatch, oversized bodies, and persistence failure
- introduce a request ID in logs and a response header; do not require clients
  to send or parse it
- introduce structured, redacted log events while preserving useful local logs
- document the threat model for admin config, API-key mutation, device Wi-Fi,
  memory mutation, source inspection, and action acknowledgement

Exit gate:

- no intentional response-shape changes
- every route has at least one success and one relevant failure contract test
- logs are demonstrably free of request credentials and Wi-Fi/API-key values

### Phase 1 — Add Internal Contracts Without Moving Behavior

Suggested branch: `refactor/bridge-contracts`

Deliverables:

- add `bridge_contracts.py`
- replace internal `RF\n...`/dict handoffs with typed model and persistence
  results where they cross major methods
- keep `parse_llm_response()` compatibility and convert its output into the
  typed internal result
- centralize conversion from typed results to the legacy `/v1` response
- add round-trip and invalid-input tests

Exit gate:

- `/v1` golden fixtures remain unchanged
- no client updates are required
- the model, service, and transport boundaries no longer rely on undocumented
  dict keys for their primary results

### Phase 2 — Extract the HTTP Transport Boundary

Suggested branch: `refactor/http-transport`

Deliverables:

- move the handler and response serialization to `http_transport.py`
- centralize JSON content-length checking, object parsing, and field validation
- centralize token authorization and device-ID extraction
- split route dispatch from endpoint implementations without changing routes
- preserve CORS headers, HTTP statuses, audio responses, and legacy JSON errors
- retain `make_handler()` as a compatibility import if tests or scripts need it

Exit gate:

- no state mutation occurs in route matching
- there is one JSON-body reader, one auth implementation, and one device-ID
  boundary validator
- every current route remains covered by the Phase 0 fixtures

### Phase 3 — Extract the Bridge Service

Suggested branch: `refactor/bridge-service`

Deliverables:

- introduce a `BridgeService` responsible for query, interaction, conversation,
  presence, and action orchestration
- move conversation/session assembly behind a conversation service or focused
  helpers with one normalized turn view
- keep `BridgeState` temporarily as the composition/facade object so rollout
  can be incremental
- inject stores, provider gateways, and clocks where deterministic tests need
  them
- separate expected domain failures from unexpected runtime failures

Exit gate:

- HTTP code only translates transport inputs/outputs
- service tests run without starting an HTTP server
- local and Supabase conversation paths return the same internal view shape

### Phase 4 — Isolate Policy and Privileged Mutations

Suggested branch: `refactor/bridge-policy-device-config`

Deliverables:

- introduce explicit policy decisions for auth principal, target-body access,
  tool eligibility, sensitive memory mutation, and admin operations
- split device Wi-Fi work into input normalization, validation, preview,
  command construction, and execution
- make preview/dry-run available to the admin path without weakening the final
  execution checks
- emit redacted audit events for API-key mutation, admin config updates, device
  config changes, durable-memory mutation, and action acknowledgement
- make privileged helpers narrow enough to test without Keychain, subprocess,
  hardware, or live Supabase access

Exit gate:

- no privileged executor performs its own authorization decision
- no policy function performs the mutation it approves
- tests prove unsafe values are rejected and secret values are absent from
  logs, errors, public results, and previews

### Phase 5 — Split the Model and Tool Pipeline

Suggested branch: `refactor/model-tool-pipeline`

Deliverables:

- move model-output parsing and cleanup to `model_protocol.py`
- represent fallback, parsed reply, tool request, and terminal reply as explicit
  internal states instead of prefixed strings
- separate context building, tool eligibility, tool execution, public result
  sanitization, and response generation
- replace long tool-summary conditionals with a declarative formatter registry
  where it improves clarity
- retain existing memory-confirmation priority, current-information search
  gating, tool-round bound, and previous-response tool loop
- add fixture tests for fenced JSON, embedded JSON, labels, malformed output,
  unknown commands, tool failures, and exhausted rounds

Exit gate:

- parser compatibility fixtures pass
- sensitive memory/source values cannot cross into public tool activity
- model/provider failures still produce the current bounded fallback behavior

### Phase 6 — Extract Configuration and Remaining Adapters

Suggested branch: `refactor/bridge-config-adapters`

Deliverables:

- move argparse/environment resolution to `bridge_config.py`
- validate interacting flags once at startup and emit a sanitized summary
- define narrow provider/store protocols where multiple implementations exist
- isolate STT/TTS/model provider calls from orchestration
- keep current local fallbacks only where an active test or workflow depends on
  them; document candidates for later removal
- reduce `wearabllm_bridge.py` to composition, compatibility exports, and `main`

Exit gate:

- startup rejects invalid combinations before serving traffic
- configuration tests cover defaults and environment overrides
- the composition root contains no route or domain business logic

### Phase 7 — Versioned Protocol Hardening

Suggested branch: `protocol/v2-envelopes`

This phase is optional until there is a concrete client need. It must not be
folded into an internal extraction PR.

Deliverables:

- freeze `/v1` as the compatibility contract
- introduce explicit `/v2` endpoints if normalized envelopes are still useful
- use `{"ok": true, "data": ...}` for success and a typed error object for
  failure in `/v2`
- publish schemas and shared fixtures consumed by bridge, Android, and Web tests
- migrate one client at a time; keep firmware on `/v1` until a firmware rollout
  is verified
- record a deprecation policy before removing any compatibility shim

Exit gate:

- mixed `/v1` and `/v2` clients can operate against one bridge
- the physical board remains functional throughout the client migration
- no `/v1` removal is scheduled without usage evidence and a rollback path

## Per-PR Verification and Rollout

Every refactor PR must:

1. show the moved responsibility and the behavior that is intentionally unchanged
2. run all bridge tests, not just the new module tests
3. run Android typecheck and protocol tests when client-facing payload code is touched
4. run `validate_protocol.py` when commands, action shapes, or protocol docs move
5. run the isolated preflight/bridge smoke for transport or service changes
6. pass `git diff --check` and a changed-file secret scan
7. deploy the candidate Space only after tests pass
8. verify `/health`, backend selection, and the deployed source revision
9. run a focused live smoke from the affected origin/body
10. keep the previous known-good Space revision available for rollback

Database migrations are not expected for the internal refactor. If one becomes
necessary, it must be backward-compatible with both the previous and candidate
bridge during rollout.

Milestone regressions should cover this matrix:

| Origin | Reply destination | Delivery | Required proof |
|---|---|---|---|
| Web | shared conversation | none | reply persists and survives refresh |
| Web | Waveshare | queued action | board reaches terminal acknowledgement |
| Android | shared conversation | none | response parses and persists |
| Android | Waveshare | queued action | app shows real delivery state |
| Waveshare | Waveshare | direct voice loop | STT, reply, display/TTS, persistence |
| Any body | unavailable storage | none | usable reply plus explicit not-saved state |

## Risk Register

| Risk | Impact | Control |
|---|---|---|
| Behavior drifts during code movement | High | Phase 0 fixtures and one-responsibility PRs |
| Auth/target checks are bypassed after extraction | Critical | one transport validator plus policy tests |
| Secrets enter structured logs or audits | Critical | redaction tests and allowlisted event fields |
| Service extraction creates a new god object | High | narrow use cases and injected adapter interfaces |
| New types imply validation that does not exist | Medium | explicit boundary validators and negative tests |
| Envelope cleanup breaks firmware or older clients | High | defer to explicit `/v2`; freeze `/v1` |
| Broad exception cleanup removes useful fallback | High | classify errors only after fallback fixtures exist |
| Wi-Fi preview leaks the password or executable command | High | redact secrets and preview normalized intent only |
| Refactor mixes with product features | Medium | separate branches and explicit non-goals |
| Live Space diverges from GitHub source | High | record Git SHA, Space SHA, and migration head per rollout |

## Success Criteria

The core refactor is complete when:

- HTTP transport, service orchestration, policy, contracts, privileged device
  configuration, and provider/storage adapters have explicit boundaries
- route matching contains no business logic and domain services contain no HTTP
  knowledge
- request parsing, auth, and target-device validation each have one canonical path
- every client-facing route has compatibility fixtures
- every privileged mutation emits a redacted audit event
- model replies and persistence results use typed internal contracts
- all current hardware and client flows still work with `/v1`
- `wearabllm_bridge.py` is a composition root rather than the place new product
  behavior is added
- tests make a regression easier to localize than it is in the current monolith

Line count is a useful signal, not the definition of success. Moving 2,901
lines into equally coupled files does not satisfy this roadmap.

## Non-Goals

Keep these out of the refactor branches unless a safety fix is unavoidable:

- dashboard hosting
- Android distribution/signing upgrades
- firmware OTA
- per-device credential rollout
- new memory-management UI
- archive/search/product UX additions
- new sensors or device capabilities
- model/provider changes
- removing local fallback modes without usage evidence

Those remain product and operations roadmap items. This roadmap creates safer
foundations for them; it does not silently bundle them into architectural work.

## First Concrete Slice

Start with Phase 0 on a new branch after selecting the reviewed PR #6 base:

1. write the endpoint/auth/shape inventory
2. capture golden `/v1` fixtures from the existing handler tests
3. add request IDs and redacted structured logs behind the same behavior
4. add the privileged-endpoint threat model
5. deploy and run the Web/Android/Waveshare regression matrix before extracting
   any production method

That slice creates the safety net needed for every later move and is independently
valuable even if the larger refactor pauses.

Phase 0 evidence is recorded in:

- `BRIDGE_PHASE0_BASELINE.md`
- `BRIDGE_API_CONTRACT.md`
- `BRIDGE_THREAT_MODEL.md`
- `../bridge/contract_fixtures/v1/golden_shapes.json`
- `../bridge/test_bridge_contracts.py`
- `../bridge/test_observability.py`
