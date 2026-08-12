# Bridge Phase 5 Model and Tool Pipeline

Recorded: 2026-08-12
Refactor branch: `refactor/model-tool-pipeline`
Stacked base: `refactor/bridge-policy-device-config` at `03bfbb4`

Phase 5 extracts model parsing, turn planning, bounded tool orchestration, and
tool-result projection from `wearabllm_bridge.py`. The existing `/v1` HTTP
contracts, model prompt, tool schemas, stores, and physical-device behavior are
unchanged.

## Model Protocol

`bridge/model_protocol.py` now owns provider-output parsing and exposes explicit
internal states for:

- fallback output, including its reason
- parsed replies, including the successful parsing strategy
- function-tool requests with call IDs and raw arguments
- terminal replies with their response ID and text

The legacy parser entry points remain importable through
`wearabllm_bridge.py`. Versioned fixtures cover leading and labeled commands,
fenced JSON, embedded JSON, embedded command labels, malformed JSON, unknown
commands, and empty output.

## Context, Eligibility, and Execution

`bridge/model_pipeline.py` separates three previously interleaved concerns:

- `build_model_request_context` normalizes history, adds the current user turn,
  and scopes retrieved memory and tool instructions to model instructions
- `build_tool_turn_plan` applies Phase 4 policy to memory, source, and current-
  information search eligibility before the provider receives any tool schema
- `ModelToolPipeline` executes the injected provider and tool adapters through
  the existing bounded Responses loop

The loop retains `parallel_tool_calls=False`, the configured tool-round bound,
`previous_response_id` continuation, function-call outputs, search-source
collection, and the reset from a forced first tool to `auto`. Pending memory
confirmation still takes priority, while an explicit combined search-and-
remember request can search before its memory mutation.

Provider failures preserve the exact initial and follow-up fallback messages.
An exhausted loop preserves the existing per-message-limit response and emits
the existing structured warning.

## Public and Private Tool Results

`bridge/tool_activity.py` now has a declarative summary-formatter registry. It
builds two deliberately different projections from each tool result:

- public activity contains bounded status, counts, identifiers, and sanitized
  errors suitable for `/v1` clients and persisted public metadata
- private model context contains the bounded arguments and result already shown
  to the model during that turn

Memory contents, memory-search queries, source-file contents, and provider or
backend error details are excluded from public tool activity. The model still
receives the exact bounded result it needs to finish the turn. Existing public
fields such as creation/saved flags, memory IDs, match counts, action IDs, body
counts, source paths, and line ranges remain available.

`BridgeState` remains the compatibility and composition facade. Its legacy
parser and tool-activity helpers delegate to the extracted modules so existing
tests and callers do not need coordinated import changes.

## Verification

The Phase 5 candidate passed:

- 217 bridge tests on Python 3.12, including direct model-protocol, context,
  eligibility, execution, sanitization, failure, and round-limit tests
- 12 bench-helper tests
- parser compatibility fixtures for all requested output shapes
- source-bundle and Docker staging checks for all three Phase 5 modules
- Hugging Face deployment dry-run including all Phase 5 runtime files
- unchanged `bridge/contract_fixtures/v1/golden_shapes.json`

Firmware code and device behavior did not change. Firmware build/flash, the
deferred Phase 0 physical Waveshare exit check, and a live hosted deployment are
not claimed here.

## Phase 6 Handoff

Runtime configuration, provider selection, STT/TTS/model calls, and process
startup remain in `wearabllm_bridge.py`. Phase 6 can extract configuration and
provider/store adapter protocols without reopening the model-tool state machine
or duplicating policy decisions inside those adapters.
