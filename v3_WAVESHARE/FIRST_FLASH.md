# WearabLLM v3 First Flash

This guide is for the first firmware flash and board-to-bridge test on the
Waveshare ESP32-S3-AUDIO-Board. It assumes no prior ESP32 experience.

The first test intentionally leaves the external TFT, speaker output, and TTS
disabled. It proves this smaller path first:

```text
hold BOOT -> white LED ring -> onboard mic capture -> local dry-run bridge
          -> response command -> LED ring color
```

The external TFT does not need to be connected for this test.

## 1. Check Hardware Before Power

Disconnect USB before changing or checking wiring.

If any perfboard or TFT wiring is attached, use a multimeter to confirm:

1. There is no continuity between `3V3` and `GND`.
2. TFT `VCC` connects only to `3V3`.
3. TFT `GND` connects to board `GND`.
4. No cut wire, solder bridge, or exposed pin can touch a neighboring contact.

The TFT may instead remain completely disconnected. The onboard RGB ring,
BOOT button, microphone codec, Wi-Fi, and audio codec do not depend on it.

For the first test, power the board only through USB. Do not also attach an
external power supply.

## 2. Connect The Board

Use a USB-C cable that supports data, not a charge-only cable. Connect it
directly to the Mac where possible.

Open Terminal and enter:

```bash
cd $HOME/Projects/wearabLLM/WearabLLM/v3_WAVESHARE
./scripts/bringup_info.py
```

Under `Candidate ESP32 serial ports`, look for a path similar to:

```text
/dev/cu.usbmodem101
```

The number can change after reconnecting the board. Always use the path shown
on the current run.

If no port appears:

1. Try another known data-capable USB cable.
2. Try another Mac USB port.
3. Temporarily remove USB hubs or adapters.
4. Tap the board's `RESET` button once.
5. Run `ls /dev/cu.*` before and after reconnecting and compare the lists.

Do not continue until a USB modem or USB serial port appears.

## 3. Check The Staged Firmware Configuration

Run this from `v3_WAVESHARE`:

```bash
./scripts/bench_doctor.py
```

The doctor does not flash, reset, or otherwise touch the board. It checks the
ignored local Wi-Fi configuration, the bridge URL, the current Mac LAN IP, and
the bridge health endpoint.

If it reports a stale bridge address, run the exact command it prints. It will
look like:

```bash
./scripts/configure_firmware.py --bridge-host 192.168.x.x
```

Then verify the result:

```bash
./scripts/configure_firmware.py --status
```

For this first test, confirm the status shows:

```text
ready for board-to-bridge dry-run test: yes
speaker output enabled: no
TTS playback enabled: no
TFT display enabled: no
```

The local `firmware/sdkconfig` file is ignored by git. The status command does
not print the Wi-Fi password.

## 4. Start The Dry-Run Bridge

Open a second Terminal window and keep it open during the device test:

```bash
cd $HOME/Projects/wearabLLM/WearabLLM/v3_WAVESHARE
./scripts/run_bridge_dryrun.sh
```

Dry-run mode receives the board's WAV recording and returns a test LED command
without using OpenAI or incurring API charges.

Open a third Terminal window and verify the bridge:

```bash
cd $HOME/Projects/wearabLLM/WearabLLM/v3_WAVESHARE
./scripts/bridge_smoke.sh
```

Do not flash for the full interaction test until the smoke check passes.

## 5. Build The Firmware

Return to the first Terminal:

```bash
cd $HOME/Projects/wearabLLM/WearabLLM/v3_WAVESHARE
./scripts/firmware_build.sh
```

The first build can take several minutes. Warnings may be printed, but the
command must finish successfully before flashing. If it ends with an error, do
not flash an older build accidentally; save the complete error output.

## 6. Flash And Monitor

Use the exact serial path reported by `bringup_info.py`, for example:

```bash
WEARABLLM_FIRST_FLASH=1 ./scripts/firmware_flash_monitor.sh /dev/cu.usbmodem101
```

Before opening the serial port, the helper verifies that the binary is newer
than its firmware inputs, contains the staged bridge URL, matches the generated
build configuration, and still has TFT/speaker/TTS/self-test paths disabled.
It refuses to flash when any of those checks fail.

The script:

1. Loads the ESP-IDF environment.
2. Writes the built firmware to the ESP32 flash.
3. Resets the ESP32.
4. Opens the serial monitor so boot and interaction logs are visible.

Do not disconnect USB while the write percentages are advancing.

### If Flashing Stays At `Connecting...`

The board normally enters download mode automatically. If it does not:

1. Hold the board's `BOOT` button.
2. Briefly press and release `RESET` while still holding `BOOT`.
3. Release `BOOT`.
4. Run the flash command again.

If needed, begin holding `BOOT` while the tool displays `Connecting...`, tap
`RESET`, and release `BOOT` after the tool starts communicating.

Erasing the entire flash is not normally required for this project.

To exit the serial monitor later, press:

```text
Ctrl+]
```

## 7. Confirm First Boot

Successful startup should include lines similar to:

```text
WearabLLM v3 Waveshare phase-1 scaffold
Wi-Fi connected: <board-ip>
ES7210 microphone capture ready
PTT GPIO=0 active_level=0 pull=pull-up
```

The display-disabled line is expected:

```text
display disabled; using serial logs only
```

That line is not an error and does not disable the LEDs, push-to-talk, mic,
network request, or response processing.

## 8. Run The First Interaction

Keep the dry-run bridge running, then:

1. Hold the board's `BOOT` button.
2. Speak a short phrase for about two seconds.
3. Release `BOOT`.

Expected board behavior:

```text
button held       -> RGB ring turns white
button released   -> thinking state
bridge response   -> dry-run response color, normally blue
```

Expected serial evidence includes:

```text
push-to-talk held: listening
capture stats: duration=... peak=... rms=... appears_silent=no
ES7210 packed lane 0: peak=... rms=... appears_silent=no
posting WAV to bridge
bridge HTTP result: err=ESP_OK status=200
LED command: BS
interaction #1 complete result=ok total_ms=... command=BS capture_source=onboard-mic
```

Then inspect the WAV received from the physical board:

```bash
python3 scripts/inspect_captures.py --latest
open bridge/captures
```

Do not move to live STT until the saved WAV is valid and contains audible
speech. If `appears_silent=yes`, debug the microphone path first.

The firmware prints one `ES7210 packed lane` line for each of the four onboard
microphones before the final mixed-WAV statistics. A silent lane points to a
codec/channel problem; non-silent lanes with a silent final mix can indicate
phase cancellation and should be reported with the complete four-line output.
The final interaction line must say `capture_source=onboard-mic`; a successful
dry-run response with `silent-fallback` proves networking and LEDs, not the mic.

## 9. Move From Dry Run To Live STT And LLM

Once the physical-board WAV sounds correct, stop the dry-run bridge with
`Ctrl+C`. Start the live bridge in the bridge virtual environment with an
`OPENAI_API_KEY` set in that terminal. The firmware does not need another flash
as long as the bridge host and port remain unchanged.

The Waveshare board does not provide general-purpose native speech-to-text.
The board records WAV audio; the bridge sends it to the configured STT service
and then asks the LLM for the reply and valence command.

## 10. TFT And Voice Response Are Separate Milestones

The TFT can remain disconnected indefinitely. The firmware display functions
become no-ops while `TFT display enabled` is `no`.

Voice response is independent of the TFT, but it is disabled for the first
flash because the physical ES8311 speaker path has not yet been verified. After
the LED/PTT/mic/bridge loop works, test the speaker output first:

```bash
./scripts/configure_firmware.py --enable-audio-out --disable-tts
./scripts/firmware_build.sh
./scripts/firmware_flash_monitor.sh /dev/cu.usbmodemXXXX
```

The speaker path uses the board's native ES8311 codec and enables its amplifier
through TCA9555 `EXIO8`; it does not require any TFT connections.

That stage tests the speaker path with the firmware response tone. After the
speaker works, enable bridge-generated speech:

```bash
./scripts/configure_firmware.py --enable-tts
./scripts/firmware_build.sh
./scripts/firmware_flash_monitor.sh /dev/cu.usbmodemXXXX
```

TTS requires the live bridge and its configured text-to-speech service. It can
produce spoken responses without any TFT attached.

## 11. If Something Fails

Do not repeatedly change several layers at once. Save a bounded serial log:

```bash
./scripts/serial_capture.sh --seconds 30 --reset /dev/cu.usbmodemXXXX
./scripts/analyze_serial_log.py
./scripts/bench_report.py
```

Report the last command run, the complete error text, LED behavior, and whether
the dry-run bridge was still open. Never post the Wi-Fi password or API key.
