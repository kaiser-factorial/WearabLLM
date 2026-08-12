# v3 Build Environment

## Local ESP-IDF

This workspace has ESP-IDF v5.5 checked out at:

```text
$HOME/Projects/wearabLLM/.toolchains/esp-idf-v5.5
```

The install was run with Codex's bundled Python 3.12 first on `PATH`, because Homebrew `python3` is currently Python 3.14 and failed to create the ESP-IDF virtual environment.

```bash
PATH="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin:$PATH" \
  $HOME/Projects/wearabLLM/.toolchains/esp-idf-v5.5/install.sh esp32s3
```

Host tools installed with Homebrew:

```bash
brew install cmake ninja
```

## Build Command

Inside Codex's restricted macOS sandbox only, ESP-IDF's component manager may
fail while listing host processes through `psutil`. The opt-in local shim keeps
the component manager on its built-in `os.getppid()` fallback:

```bash
WEARABLLM_IDF_SANDBOX=1 \
PYTHONPATH="$PWD/scripts/idf_sandbox_shim${PYTHONPATH:+:$PYTHONPATH}" \
./scripts/firmware_build.sh
```

Do not set `WEARABLLM_IDF_SANDBOX` for a normal Terminal build; the ordinary
`firmware_build.sh` command remains the supported user path.

Before a bench session, run the v3 preflight:

```bash
cd vC_WAVESHARE
./scripts/preflight.sh
```

For a faster software-only pass without rebuilding firmware:

```bash
./scripts/preflight.sh --skip-firmware
```

To check local bench readiness without flashing or resetting the board:

```bash
./scripts/bench_doctor.py
```

From the repo root:

```bash
cd vC_WAVESHARE
./scripts/firmware_build.sh
```

To compile-check optional firmware paths without changing the local
`firmware/sdkconfig`:

```bash
./scripts/firmware_variant_build.sh display
./scripts/firmware_variant_build.sh display-test
./scripts/firmware_variant_build.sh audio-out
./scripts/firmware_variant_build.sh tts
```

To run all firmware build variants:

```bash
./scripts/firmware_variant_build.sh all
```

Raw equivalent:

```bash
cd vC_WAVESHARE/firmware
PATH="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin:$PATH" \
  bash -c '. $HOME/Projects/wearabLLM/.toolchains/esp-idf-v5.5/export.sh && idf.py build'
```

For an interactive shell:

```bash
export PATH="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin:$PATH"
. $HOME/Projects/wearabLLM/.toolchains/esp-idf-v5.5/export.sh
idf.py build
```

## Bridge Smoke Command

With the bridge already running:

```bash
cd vC_WAVESHARE
./scripts/bridge_smoke.sh
```

To target a different host/port:

```bash
./scripts/bridge_smoke.sh http://192.168.1.23:8765
```

## Flash Command

After setting Wi-Fi and bridge URL in `idf.py menuconfig`:

```bash
cd vC_WAVESHARE
./scripts/firmware_flash_monitor.sh PORT
```

Replace `PORT` with the board serial device, for example `/dev/cu.usbmodem...`.
If `PORT` is omitted, the helper tries the common macOS USB serial patterns.
