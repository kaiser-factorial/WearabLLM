# Bridge Phase 6 Configuration and Provider Adapters

Recorded: 2026-08-12
Refactor branch: `refactor/bridge-config-adapters`
Stacked base: `refactor/model-tool-pipeline` at `37a5461`

Phase 6 removes CLI/environment resolution and direct provider SDK/HTTP calls
from the bridge composition facade. It preserves the existing `/v1` protocol,
runtime defaults, local development paths, hosted backends, and compatibility
exports.

## Configuration Boundary

`bridge/bridge_config.py` now owns:

- all argparse declarations and environment-derived defaults
- startup validation before stores, providers, or the HTTP server are created
- a bounded startup summary containing no tokens, credentials, paths, prompts,
  or user content

Startup now fails before serving traffic when:

- the selected model or STT provider key is absent outside dry-run mode
- hosted mode has no device token or enables local content logging
- web search is requested with a provider that cannot execute the Responses
  search tool
- a selected Supabase backend lacks its URL or service-role credential
- the port, audio limit, or action-lease interval is invalid

The startup summary uses the existing structured-log field allowlist. Backend,
search, authentication, hosted, and device-config states are emitted as
separate content-free events rather than widening the logger with ambiguous or
sensitive field names.

`wearabllm_bridge.parse_args` remains as a compatibility delegate for scripts
and tests, but contains no parser declarations or environment resolution.

## Provider Adapters

`bridge/provider_adapters.py` defines narrow structural interfaces and injected
adapters for:

- OpenAI Responses and OpenRouter chat-completion text generation
- bounded Responses tool continuation
- OpenAI household-memory embeddings
- OpenAI and OpenRouter transcription
- optional local Whisper transcription
- OpenAI-compatible TTS with provider-specific instruction support
- provider-client creation and live model discovery

`BridgeState.openai_client` deliberately remains the hot-swappable compatibility
hook used by API-key replacement and existing tests. Each call builds a narrow
adapter around the current client, so a validated key replacement takes effect
immediately without duplicating clients or provider state.

## Store Ports and Retained Fallbacks

`bridge/bridge_ports.py` defines structural ports for the action queue,
conversation store, and durable-memory store. The local JSON and Supabase
implementations satisfy the same application-facing contracts without either
implementation importing the service layer.

The following fallbacks remain intentionally supported:

- local JSON conversations and actions power the isolated bridge smoke and
  laptop development
- local JSON durable memory remains the safe fallback when the optional MEM
  database cannot open
- local Whisper remains an explicit offline STT choice and is loaded lazily
- macOS Keychain replacement remains the local dashboard path; hosted key
  replacement stays in Space Secrets

These are not deprecated in Phase 6 because active tests or documented
workflows still depend on them. A later cleanup should require usage evidence
and a separately reviewed deprecation decision.

## Composition and Compatibility

`wearabllm_bridge.py` is now 1,815 lines, down from 2,072 after Phase 5 and
2,901 at roadmap creation. It remains the composition/compatibility facade:

- constructs concrete stores, policy, services, and provider clients
- delegates HTTP handling to `http_transport.py`
- delegates application operations to `bridge_service.py`
- delegates model/tool execution to the Phase 5 pipeline
- re-exports legacy parser, helper, configuration, and provider constants
- starts and closes the HTTP server

No client-facing route or frozen response shape changed.

## Verification

The Phase 6 candidate passed:

- 225 bridge tests on Python 3.12, including configuration defaults,
  environment overrides, invalid startup combinations, summary redaction, and
  direct OpenAI/OpenRouter adapter tests
- 12 bench-helper tests
- Android bridge protocol tests and TypeScript typecheck
- protocol consistency and Python/shell syntax checks
- isolated dry-run HTTP, audio, action, and TTS smoke
- Hugging Face deployment dry-run containing all Phase 6 modules
- unchanged `bridge/contract_fixtures/v1/golden_shapes.json`
- `git diff --check`

The physical Waveshare regression passed immediately before this branch at
commit `37a5461`: fresh flash, PSRAM/display/codecs/Wi-Fi/TLS boot, targeted
action through terminal `played`, and a non-silent live voice turn through STT,
model response, LED/display, transcript upload, and TTS. Phase 6 changes no
firmware code, so another flash is not required for this software-only branch.

## Phase 7 Decision Point

The internal refactor roadmap is complete. Phase 7 is optional protocol work,
not cleanup: keep `/v1` frozen unless a concrete client need justifies parallel
`/v2` envelopes, fixtures, and one-client-at-a-time migration.
