# Agent notes — WearabLLM

## pnpm (vC Android app, converted 2026-09-18)

`vC_WAVESHARE/app` uses **pnpm**, not npm.

- Install: `cd vC_WAVESHARE/app && pnpm install`
- Check: `pnpm typecheck` / `pnpm test:protocol`
- Native: `pnpm android` (needs JDK 17 + Android SDK). `android/` is generated and gitignored; recreate with `pnpm exec expo prebuild --platform android`.
- `.npmrc` sets `node-linker=hoisted` — Expo/Metro need a flat `node_modules`. The content-addressable store still applies.
- `pnpm-workspace.yaml` is **not** a monorepo; it only holds `allowBuilds`.
- `vA_claudeWearable/phone-app` is still npm (no `node_modules` on disk; has `patch-package`). Do not convert it as part of a vC cleanup.
- Firmware / bridge / dashboard are Python + ESP-IDF. Leave them alone.

**Disk trap:** after `pnpm android`, Gradle writes intermediates into `node_modules/expo-modules-core/android/build` and `.cxx` (this tree hit 1.7 GB). That is not JS. Delete `node_modules` and `pnpm install` again; also wipe gitignored `vC_WAVESHARE/app/android/app/build`.

See `vC_WAVESHARE/app/README.md`.
