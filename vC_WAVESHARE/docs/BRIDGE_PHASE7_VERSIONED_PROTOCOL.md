# Bridge Phase 7 Versioned Protocol Hardening

Recorded: 2026-08-12
Branch: `protocol/v2-envelopes`

Phase 7 adds a parallel protocol boundary. It does not reinterpret or remove
the frozen `/v1` contract.

## Contract

Every JSON `/v1` route has a `/v2` alias handled by the same authentication,
validation, policy, service, and adapter path. Only serialization differs:

- success: `{"ok": true, "data": {...}}`
- failure: `{"ok": false, "error": {"code", "message", "request_id"}}`

`/v2/health` aliases public `/health`. Successful `/v2/tts` remains raw WAV;
its failures use the typed JSON error envelope. Endpoint data remains an object
and preserves the fields already covered by the `/v1` fixtures.

The schema, representative fixtures, and route inventory are published under
`protocol/v2/`. Bridge, Android, and Web-console tests consume the same fixture
file so examples cannot silently diverge between clients.

## First Client Migration

Android is the first `/v2` client. Its protocol boundary unwraps and validates
the envelope before passing endpoint data to the existing typed parsers. Typed
error messages remain bounded and readable. Bridge URL normalization accepts
both `/v1/query` firmware URLs and `/v2/query` client URLs.

Web remains on `/v1` for this phase. Its protocol test consumes the v2 fixtures
to establish forward compatibility without turning on a second client in the
same rollout. Waveshare remains entirely on `/v1`; no firmware source or image
configuration changes.

The mixed-version integration test creates an action through Android's v2 path
and claims it through the board's v1 path. This proves both versions share one
service and action queue rather than creating parallel product state.

## Deprecation and Rollback Policy

There is no `/v1` removal date.

Removing any `/v1` route requires all of the following in a separate reviewed
change:

1. every known client has a verified `/v2` release and named rollback owner;
2. the physical board has passed its complete v2 voice/action regression;
3. production usage evidence shows the route has no remaining v1 caller;
4. the previous bridge revision remains deployable during the observation
   window;
5. the removal and client fallback are documented before deployment.

Until those gates are met, `/v1` is supported indefinitely. Phase 7 rollback is
client-only: switch Android endpoints back to `/v1`; the bridge continues to
serve both versions and requires no data migration or firmware flash.

## Verification Gate

- frozen `/v1` fixtures remain byte-for-shape compatible
- v2 success, typed error, unknown route, authorization, oversized body, and
  binary TTS behavior are covered
- mixed Android-v2/Waveshare-v1 action delivery is covered
- bridge, Android, Web protocol, packaging, and isolated smoke checks pass
- firmware image freshness is verified because firmware sources are unchanged

The candidate passed 231 bridge tests, 12 helper tests, Android protocol tests
and TypeScript typecheck, Web shared-fixture tests, hosted staging/source-bundle
checks, protocol consistency, and an isolated mixed `/v1`/`/v2` bridge smoke.
The existing firmware image also passed the freshness/configuration gate with
its `/v1/query` URL unchanged.
