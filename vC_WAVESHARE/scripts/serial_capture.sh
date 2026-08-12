#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V3_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_DIR="$(cd "${V3_DIR}/.." && pwd)"
WORKSPACE_DIR="$(cd "${REPO_DIR}/.." && pwd)"
IDF_PATH_DEFAULT="${WORKSPACE_DIR}/.toolchains/esp-idf-v5.5"
IDF_PATH="${IDF_PATH:-${IDF_PATH_DEFAULT}}"
CODEX_PYTHON_BIN="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin"
PORT="${ESPPORT:-}"
DURATION_SECONDS=20
RESET_BEFORE=0
OUT_DIR="${V3_DIR}/logs"
OUT_FILE=""

usage() {
    cat <<'EOF'
Usage: ./scripts/serial_capture.sh [options] [PORT]

Capture ESP32 serial logs for a bounded time and save them to vC_WAVESHARE/logs/.

Options:
  --seconds N      Capture duration in seconds. Default: 20.
  --out FILE       Output log path. Default: logs/serial-YYYYmmdd-HHMMSS.log.
  --reset          Reset/run the ESP32-S3 before capturing.
  -h, --help       Show this help.

PORT can also be provided with ESPPORT. If omitted, common macOS USB serial
ports are auto-detected.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --seconds)
            DURATION_SECONDS="${2:?--seconds requires a value}"
            shift
            ;;
        --out)
            OUT_FILE="${2:?--out requires a value}"
            shift
            ;;
        --reset)
            RESET_BEFORE=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
        *)
            PORT="$1"
            ;;
    esac
    shift
done

if [ ! -f "${IDF_PATH}/export.sh" ]; then
    echo "ESP-IDF export.sh not found at: ${IDF_PATH}" >&2
    echo "Set IDF_PATH to your ESP-IDF checkout and retry." >&2
    exit 1
fi

if [ -z "${PORT}" ]; then
    for candidate in /dev/cu.usbmodem* /dev/tty.usbmodem* /dev/cu.usbserial* /dev/tty.usbserial*; do
        if [ -e "${candidate}" ]; then
            PORT="${candidate}"
            break
        fi
    done
fi

if [ -z "${PORT}" ]; then
    echo "No ESP32 serial port found." >&2
    echo "Pass a port explicitly, for example:" >&2
    echo "  ${0} /dev/cu.usbmodem101" >&2
    exit 1
fi

if [ -z "${OUT_FILE}" ]; then
    mkdir -p "${OUT_DIR}"
    OUT_FILE="${OUT_DIR}/serial-$(date +%Y%m%d-%H%M%S).log"
else
    mkdir -p "$(dirname "${OUT_FILE}")"
fi

if [ -d "${CODEX_PYTHON_BIN}" ]; then
    export PATH="${CODEX_PYTHON_BIN}:${PATH}"
fi

# shellcheck disable=SC1091
source "${IDF_PATH}/export.sh" >/dev/null

if [ "${RESET_BEFORE}" = "1" ]; then
    python -m esptool --chip esp32s3 -p "${PORT}" run >/dev/null
fi

python - "${PORT}" "${DURATION_SECONDS}" "${OUT_FILE}" <<'PY'
from __future__ import annotations

import pathlib
import sys
import time

import serial

port = sys.argv[1]
duration = float(sys.argv[2])
out_path = pathlib.Path(sys.argv[3])
deadline = time.monotonic() + duration

print(f"Capturing {port} for {duration:g}s -> {out_path}")
with serial.Serial(port, 115200, timeout=0.25) as ser, out_path.open("wb") as out_file:
    while time.monotonic() < deadline:
        data = ser.read(4096)
        if not data:
            continue
        out_file.write(data)
        out_file.flush()
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

print(f"\nSaved serial log: {out_path}")
PY
