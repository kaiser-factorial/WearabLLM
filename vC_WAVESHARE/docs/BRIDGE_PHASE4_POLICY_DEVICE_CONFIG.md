# Bridge Refactor Phase 4: Policy and Privileged Mutations

Status: implemented and software-verified on 2026-08-12

Branch: `refactor/bridge-policy-device-config`

Base: `refactor/bridge-service`

## Result

Phase 4 separates allow/deny decisions from the code that performs privileged
mutations. Existing authenticated clients retain their response shapes and the
shared device token retains its current admin compatibility during this
incremental rollout.

The new boundaries are:

- `bridge_policy.py`: pure principals, policy grants, target-body access,
  tool-intent eligibility, allowlisted targets, and sensitive-memory outcomes
- `privileged_service.py`: audited admin configuration, API-key, and device
  configuration mutations that require a matching prior grant
- `device_config.py`: Wi-Fi input normalization, validation, redacted preview,
  command construction, environment construction at execution time, and the
  subprocess adapter

`BridgeState` remains the compatibility and composition facade. HTTP routes
obtain policy grants before calling privileged methods. `BridgeService` and the
device-config executor no longer decide whether a caller is authorized.

## Compatibility Decision

The current protocol has one shared device token rather than separate admin
credentials. `BridgePolicy(shared_token_grants_admin=True)` makes that existing
behavior explicit instead of silently changing which dashboard or device
clients can use admin endpoints. The policy already supports an allowlisted
admin-device mode for a later credential migration.

Target-body operations are narrower: an authenticated principal may claim,
acknowledge, or register capabilities only for its own device ID. The grant is
bound to that target before queue or sensor mutation occurs.

## Device Configuration Preview

Authenticated admin callers can add either `"preview": true` or
`"dry_run": true` to `POST /v1/device_wifi`. Preview performs the same input
normalization, validation, and command construction as execution but does not
run the helper or require `--allow-device-config`.

Preview responses expose only booleans, bounded hardware options, and a command
whose Wi-Fi values are supplied later through the child environment. They do
not contain the SSID or password. Final execution still requires
`--allow-device-config`, and the legacy success response remains compatible.

Unsafe control characters, oversized Wi-Fi values, malformed BSSIDs, invalid
GPIO and hardware ranges, nested flag values, and mismatched grants are rejected
before subprocess execution.

## Audit and Redaction

Structured `audit.privileged_operation` events now cover:

- admin configuration mutation
- OpenAI API-key replacement
- device configuration preview and execution
- rich and local durable-memory mutation
- action acknowledgement

Events contain only allowlisted metadata such as operation, outcome, device ID,
error code, and action status. Tests prove API keys, Wi-Fi credentials, memory
content, admin values, provider error text, and helper output are absent from
audit events, previews, commands, and public error responses.

## Verification

- 204 bridge tests passed
- 12 bench-helper tests passed
- Android bridge protocol tests passed
- TypeScript typecheck passed
- Python and shell compile checks passed
- protocol consistency passed
- isolated dry-run HTTP, audio, action, and TTS smoke passed
- Hugging Face deployment dry-run includes all three Phase 4 modules
- `bridge/contract_fixtures/v1/golden_shapes.json` is unchanged
- `git diff --check` passed

Firmware code and device behavior did not change. Firmware build/flash, the
deferred Phase 0 physical Waveshare exit check, and a live hosted deployment are
not claimed here.

## Phase 5 Handoff

The model/tool pipeline remains in `wearabllm_bridge.py` and `sphere_tools.py`.
Phase 5 can extract model protocol states and the bounded tool loop while using
the Phase 4 policy methods for tool eligibility, target allowlists, and memory
mutation decisions rather than recreating those checks inside executors.
