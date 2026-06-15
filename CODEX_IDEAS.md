# Codex Ideas

This file is a parking lot for project ideas that come up while working on WearabLLM.

The ideas here are not commitments or active implementation plans. They are meant to preserve potentially useful directions, odd experiments, and "what if" thoughts that might be worth revisiting later.

## Implemented

### Speaker Earcon Bring-Up

Added an optional ES8311 speaker-output path for a short response tone after a successful bridge reply. This is intentionally just an earcon scaffold; full TTS still belongs in a later bridge/app audio pipeline.

## Potential

### Removable Display Backpack

Build the TFT perfboard adapter as a removable "display backpack" for the Waveshare ESP32-S3-AUDIO-Board. The adapter could expose the TFT, a volume knob, and a couple of extra controls while keeping the main board replaceable.

### Hardware Bring-Up Checklist

Create a small bring-up checklist for each hardware module:

- continuity check before power
- power-only test
- minimal firmware smoke test
- integration test with the rest of the system
- known-good pin map

This would make it easier to avoid rewiring mistakes as the prototype grows.

### Mood Dial Or Volume Dial

Use a potentiometer not only for speaker volume, but optionally as a "mood", "confidence threshold", or "oracle intensity" control. The LLM output mapping could change depending on that dial.

### Tiny Status Grammar

Define a compact visual grammar for the TFT plus LED ring:

- color = semantic category
- number of lit LEDs = confidence
- pulse speed = urgency or emotional intensity
- TFT text = short answer or state label

This could keep the device expressive without requiring long text on a tiny screen.

### Personality Test Mode

Add a hardware-only demo mode that cycles through response personalities, LED animations, and TFT states without needing the phone app or LLM API. Useful for showing the device and debugging the physical interface.

### TTS Audio Cache

When TTS arrives, cache a few generated phrases or earcons on the phone/host bridge and serve them to the board as small WAV chunks. This could avoid regenerating common phrases like "yes", "no", or "tell me more" while keeping the ESP32 firmware simple.

### Field Notes Log

Keep a lightweight lab log for wiring attempts, photos, firmware sketches, and "this worked / this smoked / this was confusing" notes. This could live next to `HARDWARE.md` and prevent repeated pinout detective work.
