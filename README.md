# WearabLLM

WearabLLM is a wearable LLM interface that began on an Adafruit Circuit Playground Bluefruit and is now moving toward an ESP32-S3 audio-focused build.

The current project direction is documented in:

- [SPEC.md](SPEC.md) for scope, objectives, architecture, and handoff context.
- [HARDWARE.md](HARDWARE.md) for known boards, peripherals, pin notes, and overhaul planning.

## Repository Layout

- `v1/` - original Bluefruit plus phone-app baseline, kept as a readable reference.
- `v2_servo_bluefruit/` - local servo-era archive. The full folder is intentionally ignored for now because this workspace contains macOS/iCloud placeholder files; see its README.
- `v3_WAVESHARE/` - target workspace for the ESP32-S3 audio-board iteration.
- `hardware_tests/` - small standalone sketches and probes for validating individual components.
