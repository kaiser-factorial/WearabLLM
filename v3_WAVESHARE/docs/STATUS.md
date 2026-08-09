# v3 Status

Last verified: 2026-08-09

## Verified on hardware

Target board:

- Waveshare ESP32-S3-AUDIO-Board, chip revision `v0.2`
- 16 MB flash and 8 MB PSRAM
- ES7210 microphone codec and ES8311 speaker codec
- seven-pixel WS2812 ring
- TFT200C 240x320 V1.3 on the ST7789 SPI path

Verified behavior:

- firmware builds with ESP-IDF 5.5 and passes the image-coherence gate
- USB serial/JTAG flashing works
- bootloader, app, and PSRAM memory test pass
- ST7789 display driver initializes with the current pin map
- ES7210 capture and ES8311 playback initialize
- WakeNet9 loads and `Hi ESP` becomes ready
- GPIO0/BOOT push-to-talk works
- board joins home Wi-Fi and reaches the hosted HF Space over validated TLS
- authenticated remote-action polling updates body presence
- dashboard/Android-targeted replies reach Waveshare display/TTS
- long replies use sentence/chunk display with next-chunk TTS prefetch
- a real board-originated voice interaction completed successfully, confirmed
  by the user

Final verified hosted firmware profile:

```text
device: wearabllm-esp32
query:  https://brick-factorial-wearabllm-agent.hf.space/v1/query
TTS:    https://brick-factorial-wearabllm-agent.hf.space/v1/tts
direct OpenAI: disabled
display/audio/TTS: enabled
```

The ignored local `firmware/sdkconfig` contains Wi-Fi and device credentials.
Do not publish it or distribute compiled binaries from a private deployment.

## Verified software

- 79 bridge unit tests pass
- Android TypeScript and protocol tests pass
- hosted `/health` reports OpenAI + Supabase backends ready
- OpenAI model catalog and live query work through the Space
- all Supabase migrations through `20260809030000` match remote
- conversation create/read/new-session behavior works across bodies
- conversation rename and archive endpoints/UI are implemented
- one 12-turn local session was recovered into Supabase without replaying it
- live presence uses a 20-second TTL for Waveshare, Android, and Web console
- dashboard optimistic sends and inline thinking state work
- queued Waveshare interactions expose real board-reported states

## Known warnings and boundaries

- The dashboard is still served from localhost; the hosted brain itself does
  not require the laptop.
- Firmware updates still require USB; OTA is not implemented.
- Android uses a local development release/signing flow.
- The current Expo 52 dependency tree reports 29 npm advisories (10 moderate,
  18 high, 1 critical). npm's proposed resolution is a semver-major Expo and
  React Native upgrade, so it is tracked as a dedicated upgrade rather than
  forced into this hardware-verified release.
- Android and Waveshare currently share one device token.
- The richer `wearabllm_memory_records` schema is not yet connected to a
  model-facing memory tool or user review/correction UI.
- Archive restore/unarchive and deletion are not implemented.
- Boot logs emit an I2C pull-up-resistance warning even though both codecs
  initialize and physical audio works; investigate if hardware is intermittent.
- Long-response retry, lease-expiry, reboot, and power-loss behavior need a
  recorded regression matrix.

## Current verification commands

```bash
# Bridge
OPENAI_API_KEY=test-key bridge/.venv/bin/python -m unittest discover \
  -s bridge -p 'test_*.py'

# Android
cd app
npm run typecheck
npm run test:protocol

# Firmware
cd ..
./scripts/firmware_build.sh
./scripts/firmware_flash_monitor.sh /dev/cu.usbmodemXXXX

# Supabase
cd ..
supabase migration list
```

## Next verification pass

Run and record this matrix:

| Origin | Network | Waveshare delivery | Expected terminal state |
|---|---|---|---|
| Waveshare voice | home Wi-Fi | direct response | audible/displayed reply |
| Android text | Wi-Fi | off | shared reply only |
| Android text | cellular | on | `played` |
| Web console | localhost | off | shared reply only |
| Web console | localhost | on | `played` |

Include a short reply, a multi-chunk reply, board reboot during a queued action,
and a deliberate TTS failure.
