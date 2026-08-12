# Bridge Phase 1 Internal Contracts

Recorded: 2026-08-12
Refactor branch: `refactor/bridge-contracts`
Stacked base: `refactor/bridge-contract-baseline` at `b763b94`

Phase 1 makes the bridge's primary model, persistence, service, and transport
handoffs explicit without changing the legacy `/v1` protocol. Physical ESP
validation remains part of the deferred Phase 0 exit gate; this phase does not
claim that check is complete.

## Contract Boundary

`bridge/bridge_contracts.py` defines standard-library dataclass contracts for:

- parsed and generated model replies
- public tool activity, private model tool context, and web sources
- conversation persistence outcomes, including explicit write failure
- query inputs and results
- interaction inputs and results

The contracts perform runtime validation and defensively copy mutable legacy
payloads. They do not add a dependency such as Pydantic.

## Compatibility Adapters

The existing entry points remain available:

- `parse_llm_response()` returns the existing `(command, reply)` tuple while
  `parse_model_reply()` is the typed parser used internally.
- `_generate_agent_text()` retains the former raw-text/metadata tuple for
  focused compatibility tests; the live orchestration path uses
  `_generate_agent_result()`.
- `ask_llm_with_metadata()`, `answer_transcript()`, and
  `create_interaction()` retain their former tuple/dictionary forms.
- HTTP handlers use `QueryInput`/`InteractionInput` and typed service results,
  then serialize exactly once through `to_legacy_dict()` at the `/v1` edge.

Hosted deployment staging and the Docker image now include
`bridge_contracts.py`.

## Verification

The Phase 1 candidate passed:

- 176 bridge tests on Python 3.12: the 168-test Phase 0 suite unchanged plus
  eight typed round-trip, invalid-input, defensive-copy, and parser-
  compatibility tests
- all Phase 0 golden HTTP contract tests, with the fixture file unchanged
- 12 bench/helper tests
- Android bridge-protocol tests and TypeScript typecheck
- protocol consistency and Python/shell syntax checks
- isolated dry-run HTTP/audio/action/TTS smoke
- Hugging Face deployment dry-run, including `bridge_contracts.py`
- `git diff --check`

Firmware source did not change, so the preflight intentionally skipped a new
firmware build. No live bridge deployment or ESP test is claimed by this
document.

## Phase 2 Handoff

The HTTP handlers are still physically located in `wearabllm_bridge.py`, but
their query and interaction paths now consume typed service results. Phase 2
can extract that handler into `http_transport.py` while retaining
`make_handler()` and the frozen Phase 0 fixtures as compatibility gates.
