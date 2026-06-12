# WearabLLM Hardware Notes

Last updated: 2026-06-12

## Purpose

This document tracks the hardware story for WearabLLM: what has been used, what is currently in play, what should be treated as legacy, and how to approach the ESP32-S3 overhaul.

The project goal is a wearable LLM interface with physical output:

```text
voice / buttons / sensors -> app or bridge -> LLM -> LEDs / sound / screen / haptics
```

## Past And Current Hardware

### Adafruit Circuit Playground Bluefruit

Status: working historical baseline.

Used for:

- BLE connection to phone/laptop bridge
- 10 onboard NeoPixel LEDs
- onboard speaker for earcons
- Button A for tap-to-start/tap-to-stop voice control
- slide switch for sensor sharing toggle
- temperature, light, and accelerometer context
- LiPo-powered wearable testing

Why it worked well:

- fast to prototype
- BLE and expressive LEDs were built in
- no external LED wiring required
- enough onboard sensors for playful context-aware prompts

Limitations for the next iteration:

- only 10 built-in LEDs
- not ideal as the center of a richer mic/TFT/audio system
- onboard output shape is fixed by the CPB form factor
- future board migration would require rethinking pinout and firmware anyway

Current recommendation:

- keep as the known-good reference device
- use it to compare BLE protocol and response language
- do not build the full overhaul around it

### CPB Servo Reveal Mechanism

Status: legacy/experimental.

Parts and files:

- SG90-style micro servo
- 3D printed reveal/clamshell concept
- `cpb/code_reveal.py`
- `servo_bluefruit/servo_potentiometer_copy/`

What it did:

- opened a cover when the LLM responded
- held the cover open while LEDs/sounds played
- closed afterward

Current recommendation:

- remove from the main path
- keep the code as archive/reference only
- do not spend next-iteration complexity budget on servo actuation unless intentionally revived

### Phone

Status: current bridge/API device.

Used for:

- microphone input
- native speech recognition
- LLM API call
- BLE command delivery to wearable
- text response display
- settings/API key storage

Current limitation:

- the user must speak into the phone, which breaks the wearable interaction.

Next role:

- keep the phone as the API and speech-to-text bridge at first
- move microphone capture onto the wearable later
- avoid putting API keys directly on the microcontroller until the hardware loop is stable

### Laptop Bridge

Status: historical and still useful for debugging.

Used for:

- local microphone recording
- local Whisper transcription
- Claude API calls
- serial/BLE command transmission

Current recommendation:

- useful as a test harness for WiFi audio streaming
- not the final desired user experience

### NeoPixel / Addressable LEDs

Status: central to the next iteration.

Past:

- CPB's built-in 10-pixel NeoPixel ring.

Planned:

- external NeoPixel-compatible strip.

Design direction:

- color communicates semantic category
- number of lit LEDs communicates certainty/confidence
- animation communicates tone/intensity
- brightness stays capped for battery, heat, and comfort

Power note:

- do not power a meaningful LED strip from a microcontroller GPIO or weak onboard regulator
- all LED power supplies must share ground with the controller
- budget worst-case current as high as roughly 60 mA per RGB LED at full-white, then cap brightness in firmware

## Candidate Boards

### LAFVIN Nano / Arduino Nano-Style Board

Status: not recommended as the main v2 board.

Likely characteristics:

- ATmega328P-class Arduino Nano-compatible board
- micro-B USB
- no native BLE
- no native WiFi
- very limited RAM/flash compared with ESP32-S3

Good for:

- tiny isolated experiments
- button tests
- simple NeoPixel test
- servo test

Poor fit for:

- wearable mic
- TFT LCD plus SD card
- BLE app connection without an added module
- audio handling
- future expansion

Recommendation:

- do not use as the overhaul platform

### ESP32-S3

Status: recommended v2 platform.

Why it fits:

- much more compute headroom than Nano-class boards
- native WiFi
- Bluetooth Low Energy
- I2S audio input/output support
- SPI/I2C support for TFT, SD, sensors, and audio codec modules
- RMT/PWM-style peripherals suitable for NeoPixels, haptics, tones, and motor control
- enough GPIO flexibility for buttons and expansion

Important Bluetooth note:

- ESP32-S3 supports Bluetooth LE, not Bluetooth Classic audio.
- This means ordinary Bluetooth speaker/headset style audio paths such as Classic A2DP/SPP should not be assumed.
- BLE is good for commands and small packets, but raw mic audio over BLE is a larger custom engineering task.

Current recommendation:

- use ESP32-S3 as the wearable controller
- keep the phone/app as the LLM API brain initially
- use BLE for commands/status
- only introduce WiFi when the wearable mic needs to stream usable audio

### Waveshare ESP32-S3-AUDIO-Board

Status: primary next prototype target.

The board in hand is labeled `ESP32-S3-AUDIO-Board` by Waveshare. Earlier notes that assumed an Espressif `ESP32-S3-BOX-Lite` should be treated as historical confusion, not the active hardware target.

Known features from Waveshare documentation and user photos:

- ESP32-S3R8 dual-core MCU.
- 16 MB flash.
- 8 MB PSRAM.
- WiFi and Bluetooth LE.
- dual onboard microphones.
- ES7210 audio encoder for microphone capture.
- ES8311 audio codec for playback.
- power amplifier and speaker header.
- TF/microSD card slot.
- 7 circular programmable RGB LEDs.
- USB-C for power/programming.
- 3.7 V lithium battery connector with charging/discharging support.
- 18-pin screen FPC connector labeled `DISPLAY`.
- camera FPC connector labeled `CAMERA`; Waveshare docs describe this as a DVP camera interface.
- 20-pin expansion header; silkscreen says these GPIOs are already used by the screen interface.
- onboard power switch and user buttons.
- IPEX antenna connector option plus onboard ceramic antenna.

Why it is compelling:

- it already matches the "home base" assistant form: mic, speaker, LEDs, SD, display connector, camera connector, buttons, and power path are on one board.
- the LED ring can directly carry WearabLLM valence/certainty color.
- the camera connector is likely real and intended for compatible Waveshare DVP camera modules.
- the TF card can hold phrase clips, sound effects, and local UI assets.
- the board is a better first full WearabLLM interface prototype than a bare ESP32-S3 plus separate modules.

Limitations / cautions:

- The 20-pin header shares signals with the screen interface; do not assume all exposed pins are free if a display is connected.
- The 18-pin display connector is easiest with Waveshare displays that match its pinout.
- The separate 0.96" ST7735S breakout can still be wired manually, but it is not a direct plug-in match for the 18-pin `DISPLAY` FPC.
- The camera connector should use a compatible DVP camera module and matching pinout, not a random camera ribbon.
- Use Waveshare's docs and examples for this board, not the Espressif BOX-Lite BSP pin maps.

Visible 20-pin header silkscreen:

```text
Top row:    IO7  IO6  IO5  IO4  IO3  IO2  IO1  GND  GND  5V
Bottom row: EX3  EX2  EX1  IO9  IO0  SDA  SCL  GND  5V   3V3
```

Recommended project role:

- Use the Waveshare ESP32-S3-AUDIO-Board as the first full WearabLLM "home base" assistant.
- Later build the custom wearable as a second device/client using the same command protocol.
- Let the phone app or local bridge coordinate identity, conversation state, and message routing between home base and wearable.

Possible integrated system:

```text
WearabLLM wearable <-> phone app / local bridge <-> Waveshare ESP32-S3-AUDIO-Board
          |                         |                         |
     mobile LEDs/TFT           LLM/session state        home mic/speaker/LEDs/camera
```

Waveshare-first build path:

1. Confirm factory/examples: RGB ring, microphones, speaker, TF card, buttons.
2. Build a minimal RGB ring valence demo.
3. Add audio playback from generated tones or SD clips.
4. Add microphone capture / wake word with ESP-SR or ESP-Skainet.
5. Add display via the onboard `DISPLAY` FPC or the separate 0.96" ST7735S breakout.
6. Add app/bridge connection for LLM API calls.
7. Add compatible camera module after confirming exact Waveshare camera part/pinout.
8. Reuse the command protocol later on the portable wearable.

Software references for this board:

- Waveshare `ESP32-S3-AUDIO-Board` documentation and examples.
- `espressif/esp-skainet`: wake word detection, offline speech command recognition, and audio front-end processing.
- `esp-sr`: underlying Espressif speech recognition components used by ESP-Skainet.
- ESP-IDF extension for VS Code as the main development workflow.

What ESP-Skainet is good for:

- wake word detection
- local command recognition
- voice activity detection
- noise suppression
- acoustic echo cancellation
- testing the microphone path

What ESP-Skainet is not:

- general speech-to-text for arbitrary LLM prompts
- natural TTS for arbitrary LLM answers
- an LLM runtime

Recommended audio strategy:

- Use ESP-Skainet/ESP-SR for wake word and maybe a small offline command layer.
- Use phone/laptop/cloud for arbitrary speech-to-text and LLM calls.
- Use the onboard speaker for earcons, SD clips, or streamed/generated TTS.
- Keep the RGB ring as the immediate embodied valence/certainty response.

## ESP32-S3 Overhaul Plan

### Target Hardware Functions

The next wearable controller should support:

- NeoPixel strip
- wearable microphone
- optional speaker
- optional haptics
- TFT LCD with SD card
- several buttons
- BLE connection to the app
- eventual audio transfer to app or bridge
- command reception from app/LLM layer

### Recommended System Split

Initial v2 architecture:

```text
ESP32-S3 buttons/LEDs/TFT/sound
        |
       BLE
        |
phone app handles STT + LLM API
```

Wearable-mic architecture:

```text
ESP32-S3 mic/buttons/LEDs/TFT/sound
        |
BLE for control + WiFi or other transport for audio
        |
phone app or laptop bridge handles STT + LLM API
```

Avoid this until later:

```text
ESP32-S3 directly calls LLM API
```

Reasons to avoid direct API calls at first:

- API key handling is awkward on a wearable microcontroller
- WiFi onboarding becomes a product problem
- HTTPS/API reliability adds complexity
- speech-to-text still needs a destination
- BLE app bridge already exists

### Recommended Build Order

1. ESP32-S3 boots and drives a small NeoPixel strip.
2. ESP32-S3 receives BLE commands from the app.
3. Existing 9-code command set works on the strip.
4. Add v2 command fields: confidence, LED count, animation, optional display text.
5. Add buttons.
6. Add TFT LCD and display latest response/status.
7. Add speaker or haptic output.
8. Add microphone capture locally on ESP32-S3.
9. Decide audio transport: WiFi bridge, BLE custom stream, or dedicated codec/radio module.

## PinPulse ESP32-S3 Shield Considerations

Status: useful as a prototyping/debug shield, not the primary LED surface.

The user's available shield is by Lonely Binary.

Important distinction:

- The Lonely Binary ESP32-S3 board itself has a built-in WS2812 RGB LED on IO48.
- The PinPulse shield's many LEDs are described as visual GPIO indicator LEDs for HIGH/LOW debugging.
- Those PinPulse GPIO LEDs should be treated as single-purpose logic/status indicators, not as an expressive RGB LED display.

The built-in IO48 WS2812 is useful for:

- confirming firmware upload
- connection status
- quick RGB color tests
- one-pixel debug feedback

The PinPulse shield is useful for:

- seeing GPIO state changes without a multimeter
- cleaner jumper access through male/female headers
- debugging SPI/I2C/UART/PWM pin activity
- faster lab prototyping

The PinPulse shield is not sufficient for:

- the project's main color language
- confidence-by-LED-count output
- rich multi-pixel animation

Recommendation:

- use the Lonely Binary ESP32-S3 if it is the available S3 board, especially if it is the N16R8 version with 16 MB flash and 8 MB PSRAM
- use the PinPulse shield if it makes wiring easier in the lab
- still use the external NeoPixel strip as the actual user-facing color output
- do not rely on the PinPulse GPIO indicator LEDs for the wearable's response language
- if the shield blocks access to pins needed for TFT/SD/I2S/NeoPixel, remove it and wire directly

Key thing to verify:

```text
Does the shield expose a clean GPIO for an external NeoPixel strip?
Does it leave enough SPI/I2S pins available for TFT/SD/mic/audio?
```

### Lonely Binary 15-Pin Display Connector

Status: documented from user-provided pin notes; verify against the shield/board silkscreen or schematic before wiring third-party modules.

User-provided connector mapping:

| Pair as provided | Notes |
| --- | --- |
| `15-5` | signal mapping, likely connector/board label to GPIO/signal |
| `14-6` | signal mapping |
| `13-4` | signal mapping |
| `12-7` | signal mapping |
| `22-15` | signal mapping; verify because `22` may be a board label rather than a 15-pin connector position |
| `20-1` | signal mapping; verify because `20` may be a board label rather than a 15-pin connector position |
| `9-10` | signal mapping |
| `8-11` | signal mapping |
| `7-12` | signal mapping |
| `6-13` | signal mapping |
| `5-2` | signal mapping |
| `4-42` | signal mapping |
| `3-41` | signal mapping |
| `2-3V3` | 3.3 V power rail |
| `1-GND` | ground |

How to read this:

- Each pair means one side of the connector/silkscreen position is wired to the signal on the other side.
- Entries ending in plain numbers are most likely ESP32-S3 GPIO numbers or board pin labels.
- `3V3` and `GND` are power, not data pins.
- The numbering/orientation of FPC connectors is easy to mirror accidentally, so confirm pin 1 physically before plugging in any display.

What this implies:

- This looks like a display-oriented connector because it exposes power plus a bundle of GPIOs.
- It does not automatically mean a camera can be attached.
- A camera connector needs a compatible pinout and named camera signals such as `XCLK`, `PCLK`, `VSYNC`, `HREF`, and data lines, or a documented SPI/CSI camera interface.
- Unless Lonely Binary explicitly documents this as camera-compatible, treat it as a display connector only.

Practical recommendation:

- Use Lonely Binary's matching display if available.
- Do not plug in a random 15-pin camera ribbon just because the connector has 15 contacts.
- If using a third-party TFT, map its required signals (`SCK`, `MOSI`, `MISO`, `CS`, `DC`, `RST`, `BL`, `SD_CS`) to the GPIOs exposed here.
- If using a camera later, choose a known ESP32-S3 camera module/adapter with documented pin assignments.

## Audio Codec Module Considerations

Status: likely useful for audio input/output, but not a confirmed Bluetooth audio transport solution.

The user's available codec module is believed to be by LAFVIN. LAFVIN's ESP32S3 AIChatBot hardware docs describe their audio codec module as supporting voice input and output, with high signal-to-noise ratio, built-in DSP, I2S output, dual omnidirectional microphones, and sufficient volume output.

The phrase "audio codec module" usually means a hardware chip that converts between analog audio and digital audio, often over I2S. Examples include chips that provide:

- microphone preamp
- ADC for mic/line input
- DAC for headphone/speaker output
- volume/gain controls over I2C
- I2S digital audio connection to the ESP32-S3

This is useful, but it is not automatically the same as Bluetooth audio compression.

### What A Codec Module Helps With

An audio codec can help if it provides:

- cleaner mic input than a bare analog mic
- speaker/headphone output
- I2S compatibility
- onboard amplifier
- known Arduino/ESP-IDF examples

It may be worth borrowing if:

- it has a known chip number
- there are ESP32-S3 examples
- it supports the mic and speaker path you want
- it does not require too many conflicting pins

For the LAFVIN module specifically, it is worth borrowing for:

- wearable microphone experiments
- speaker playback experiments
- I2S audio path testing on ESP32-S3
- comparing against a bare INMP441 mic

### What A Codec Module Usually Does Not Solve

Most audio codec modules do not magically make BLE microphone streaming easy.

The LAFVIN docs say the module uses I2S and supports DSP/audio quality, but they do not establish that the module performs Bluetooth-ready compression or exposes a phone-compatible Bluetooth microphone profile. Assume it outputs digital audio samples that the ESP32-S3 still has to manage.

The ESP32-S3 still has to:

- capture the samples
- buffer them
- optionally compress/encode them
- transmit them over BLE or WiFi
- coordinate with the phone/app/bridge receiver

### When It Would Solve The Bluetooth Problem

The module is much more interesting if it explicitly says it supports one of these:

- Bluetooth audio source
- BLE Audio / LC3
- Classic Bluetooth A2DP source
- HFP/HSP microphone profile
- onboard encoder such as Opus, LC3, AAC, or SBC
- a complete Bluetooth audio module, not just an I2S codec

Important caveat:

- ESP32-S3 itself is BLE-only and should not be treated as a Classic Bluetooth audio source.
- If a module provides Classic Bluetooth audio, that capability is likely coming from the module's own Bluetooth chip, not from the ESP32-S3.

Recommendation:

- borrow the LAFVIN codec if available; it is likely directly relevant to mic/speaker prototyping
- do not assume it eliminates the need for WiFi or a custom BLE audio path
- do not borrow it specifically for Bluetooth compression unless the module or kit docs clearly advertise Bluetooth audio source, BLE Audio, HFP/HSP microphone mode, or onboard encoding

## Audio Transport Decision

Preferred order for near-term prototyping:

1. Keep phone mic until ESP32-S3 LEDs/TFT/buttons are stable.
2. Add ESP32-S3 mic capture and print/save/stream short PCM chunks for debugging.
3. Try WiFi audio streaming to laptop bridge first.
4. Only attempt BLE audio if the experience requires BLE-only operation and the audio requirements are modest.
5. Use a dedicated Bluetooth audio/codec module only if it has documented phone-compatible audio support.

Why WiFi may be necessary:

- mic audio is much larger than command/status data
- BLE command-style services are not designed for easy raw audio streaming
- WiFi lets the ESP32-S3 send HTTP/WebSocket/UDP audio chunks to a bridge with less custom packet gymnastics

Why avoiding WiFi is understandable:

- WiFi setup is annoying in a wearable
- battery draw is higher
- phone hotspot/router dependencies complicate demos

Pragmatic compromise:

- use BLE-only for the first ESP32-S3 overhaul
- keep mic on phone during this phase
- add WiFi only for the wearable-mic prototype
- later replace WiFi with a dedicated audio transport if a good module appears

## TFT LCD And SD Card Notes

Available displays:

### 0.96" 80 x 160 IPS SPI Display

Planned role: BOX-Lite/home-base display.

- Resolution: 80 x 160.
- Type: RGB IPS TFT.
- Driver IC: ST7735S.
- Interface: 4-wire SPI.
- No SD card mentioned.
- Good fit for compact status, short answer text, and debug UI on the BOX-Lite audio base.

Recommended BOX-Lite use:

- show current state: idle, listening, thinking, responding
- show short LLM answer text
- show selected response code and confidence
- keep graphics simple due to small resolution

Signals to plan for:

- `VCC` / `3V3`
- `GND`
- `SCL` / `SCK`
- `SDA` / `MOSI`
- `RES` / `RST`
- `DC`
- `CS`
- `BLK` / `BL`, if present

Integration notes:

- Use 3.3 V logic.
- Use hardware SPI if possible.
- The display does not need MISO unless the module exposes readback.
- Confirm the two 10-pin BOX-Lite expansion headers expose enough free GPIO for SPI plus control pins.

### Adafruit 1.8" TFT LCD With microSD Breakout

Planned role: later portable wearable display/storage module.

- Adafruit 1.8" color TFT LCD display with microSD card breakout
- Resolution: 160 x 128 / 128 x 160 depending on rotation
- Driver family: ST7735R
- Interface: 4-wire SPI for display
- microSD socket shares SPI bus with a separate card chip-select pin
- The breakout exposes normal solder/header pins, so it does not need the Lonely Binary 15-pin display connector.
- Since it has the SD card socket, reserve it for the future wearable where local audio clips/assets may matter more.

Interface:

- TFT: SPI
- SD card: SPI

Planning concerns:

- TFT and SD can often share SPI bus lines while using separate chip-select pins
- display refresh can interfere with timing-sensitive LED updates if firmware is not structured carefully
- use libraries/examples matched to ST7735R

Adafruit breakout signals to plan for:

- `Vcc` / `Vin`: display power; Adafruit breakout supports 3-5 V power
- `GND`: ground
- `SCLK` / `CLK`: SPI clock
- `MOSI`: SPI data from ESP32-S3 to display/SD
- `MISO`: SPI data from SD card back to ESP32-S3; not needed by the TFT itself
- `TFT_CS`: display chip select
- `Card CS`: microSD chip select
- `D/C`: display data/command select
- `RST`: display reset
- `Lite`: backlight control; tie high for always-on or use PWM for brightness

Recommended ESP32-S3 integration:

- Put TFT and SD on the same SPI bus.
- Give TFT and SD separate chip-select pins.
- Keep `D/C`, `RST`, and `Lite` on ordinary free GPIOs.
- Use hardware SPI rather than software SPI for speed.
- Start with the display only; add SD card reads after basic graphics work.
- Treat the TFT as a local status/answer display, not as the main interaction surface.

Potential display uses:

- connection state
- listening/thinking/responding state
- short LLM answer
- current LED command and confidence
- debugging sensor/audio status

## Speaker And Haptics Notes

Speaker options:

- simplest: piezo/buzzer tones from PWM
- analog speaker amplifier: Adafruit PAM8302A 2.5 W Class-D mono amp
- better: I2S amplifier such as MAX98357A
- best: audio codec module with DAC and amplifier

### Adafruit PAM8302A 2.5 W Class-D Amp

Status: available, function unknown.

What it is:

- Mono Class-D speaker amplifier.
- Drives a 4-8 ohm speaker directly.
- Power input: roughly 2.0-5.5 V.
- Audio input: analog audio on `A+` / `A-`.
- Output: bridge-tied speaker output; neither speaker terminal goes to ground.

Useful for:

- louder earcons
- SD-card WAV playback if the ESP32-S3 has an analog audio source
- phone/laptop analog audio test playback
- pairing with a codec/DAC analog output

Not directly useful for:

- raw I2S digital audio output
- driving headphones
- connecting output into another amplifier

ESP32-S3 integration note:

- ESP32-S3 does not have a simple built-in DAC output.
- To use the PAM8302A cleanly with ESP32-S3, feed it analog audio from a codec/DAC module, or use PWM/PDM with filtering.
- If using a single-ended analog source, connect source signal to `A+` and tie `A-` to ground.
- Do not connect either speaker output terminal to ground.

Basic test procedure:

1. Use a known 4-8 ohm speaker.
2. Connect speaker only to the amp speaker output pads/terminals.
3. Power amp from 3.3 V or 5 V and ground.
4. Tie `A-` to ground.
5. Feed quiet analog audio into `A+` from a phone/laptop headphone output through a low volume setting.
6. Turn the onboard volume trim low before powering up.
7. Slowly raise source volume and/or trim pot.
8. If sound is clean, the amp works.

Safety checks:

- Do not test without a speaker or load unless just checking power.
- Do not short speaker output to ground.
- Do not connect the speaker negative terminal to ground.
- Start volume low; the amp can be surprisingly loud.
- If the chip heats quickly, buzzes harshly, or power supply browns out, disconnect and recheck wiring.

Haptics:

- do not drive motors directly from GPIO
- use a transistor/MOSFET driver and flyback protection where appropriate
- consider a haptic driver chip if nuanced patterns matter later

### Small Audio Modules On Hand

Status: visually identified from user photo; confirm labels/chip markings before wiring.

Observed modules:

- Two red sound sensor/microphone modules with trim potentiometers and electret microphone capsules.
- One black three-pin buzzer module marked with `S`, likely a signal-controlled buzzer/speaker module.
- TMB12A05 buzzer, if present.

Likely use:

- Red modules: sound detection, loudness sensing, clap/tap detection, crude voice activity detection.
- Black module: audio output for beeps/tones if passive, or simple on/off beep if active.
- TMB12A05: simple 5 V buzzer/audio alert output.

Do not assume:

- The red modules are good enough for speech-to-text.
- The red modules provide clean PCM audio.
- The black module is a microphone.
- TMB12A05 is a microphone.

Why the red mic modules are limited:

- These common modules usually use an electret mic plus amplifier/comparator.
- The digital output is often just thresholded sound/no sound.
- The analog output is usually noisy and not ideal for intelligible speech capture.
- The trim potentiometer usually adjusts threshold or gain, not audio quality.

Recommendation:

- Use these red modules for experiments like "detect that someone spoke" or "wake on loud sound."
- Use the LAFVIN codec or an I2S mic such as INMP441 for actual wearable voice input.
- Use the black buzzer module for early earcon tests, but use an I2S amp or codec for real voice playback.
- Use the TMB12A05 only for beeps/alerts. It is not useful for audio input.

## Onboard TTS Strategy

Goal:

- Let the wearable speak some or all of the LLM response without relying entirely on phone speaker playback.

Important constraint:

- ESP32-S3 can play audio, but it cannot run modern neural TTS models such as Voxtral TTS locally.
- Natural LLM voice should be generated on phone/cloud/laptop and streamed or copied to the ESP32-S3 for playback.

Reasonable onboard options:

1. Stored phrase/audio library on SD card.
   - Most reliable and wearable-friendly.
   - Use WAV/MP3 clips such as `yes`, `no`, `maybe`, `thinking`, `warning`, `interesting`, and characterful nonverbal sounds.
   - LLM selects a clip ID, while full answer appears on TFT/phone.

2. Embedded lightweight TTS.
   - Possible for robotic English speech using engines such as PicoTTS on ESP32-S3 with PSRAM.
   - Useful if arbitrary text must be spoken offline.
   - Voice quality will be noticeably synthetic, not like modern cloud TTS.
   - Adds ESP-IDF complexity and memory pressure.

3. Cloud/phone TTS streamed to wearable.
   - Best voice quality.
   - Phone/cloud generates audio, ESP32-S3 plays the resulting stream through I2S codec/amp.
   - Likely wants WiFi or a careful custom transport because audio is much larger than BLE commands.

4. Phone speaks, wearable performs.
   - Easiest complete experience.
   - Phone plays natural TTS; wearable handles LEDs/TFT/haptics/earcons.

Recommendation:

- Start with SD phrase clips plus earcons.
- Add cloud/phone TTS later if natural speech from the wearable becomes essential.
- Try PicoTTS only if robotic onboard speech is acceptable and the ESP32-S3 board has enough PSRAM/flash.

## Power Notes

Main risks:

- LED strip current draw
- speaker amplifier current draw
- WiFi current spikes
- brownouts when LEDs/audio/WiFi fire together

Rules:

- use a battery/power path sized for peak load
- common ground across board, LEDs, audio, and haptics
- cap LED brightness in firmware early
- test with USB first, then battery
- add bulk capacitance near LED strip power if needed

## Hardware Questions To Answer Next

- Exact ESP32-S3 board model and whether it has PSRAM.
- Exact PinPulse shield model or clear photos of both sides.
- Exact TFT LCD model/controller and voltage.
- Exact audio codec module model/chip number.
- NeoPixel strip type, length, voltage, and LED count.
- Whether haptics are coin vibration motors, ERM motors, or another type.
- Battery type/capacity and whether there is a charger/protection board.

## Current Recommendation

Use the ESP32-S3 for the overhaul.

Use external NeoPixels as the main expressive LED surface, regardless of whether the PinPulse shield has onboard RGB LEDs.

Use BLE for app connection, status, and LLM response commands.

Delay wearable microphone streaming until LEDs, buttons, TFT, and basic app control are stable.

If wearable mic is required in the next prototype, use WiFi audio streaming first unless the lab's audio module is a complete Bluetooth audio module with documented phone-compatible audio support.
