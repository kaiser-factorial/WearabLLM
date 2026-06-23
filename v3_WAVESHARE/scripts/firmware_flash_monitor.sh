#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V3_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
FIRMWARE_DIR="${V3_DIR}/firmware"
REPO_DIR="$(cd "${V3_DIR}/.." && pwd)"
WORKSPACE_DIR="$(cd "${REPO_DIR}/.." && pwd)"
IDF_PATH_DEFAULT="${WORKSPACE_DIR}/.toolchains/esp-idf-v5.5"
IDF_PATH="${IDF_PATH:-${IDF_PATH_DEFAULT}}"
CODEX_PYTHON_BIN="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin"
PORT="${1:-${ESPPORT:-}}"

if [ ! -f "${IDF_PATH}/export.sh" ]; then
    echo "ESP-IDF export.sh not found at: ${IDF_PATH}" >&2
    echo "Set IDF_PATH to your ESP-IDF checkout and retry." >&2
    exit 1
fi

if [ "${WEARABLLM_FIRST_FLASH:-0}" = "1" ]; then
    python3 "${SCRIPT_DIR}/verify_firmware_image.py" --require-first-flash-profile
else
    python3 "${SCRIPT_DIR}/verify_firmware_image.py"
fi

if [ -z "${PORT}" ]; then
    for candidate in /dev/tty.usbmodem* /dev/cu.usbmodem* /dev/tty.usbserial* /dev/cu.usbserial*; do
        if [ -e "${candidate}" ]; then
            PORT="${candidate}"
            break
        fi
    done
fi

if [ -z "${PORT}" ]; then
    echo "No ESP32 serial port found." >&2
    echo "Pass a port explicitly, for example:" >&2
    echo "  ${0} /dev/tty.usbmodem1101" >&2
    exit 1
fi

cd "${FIRMWARE_DIR}"

if [ -d "${CODEX_PYTHON_BIN}" ]; then
    export PATH="${CODEX_PYTHON_BIN}:${PATH}"
fi

# shellcheck disable=SC1091
source "${IDF_PATH}/export.sh"

idf.py -p "${PORT}" flash monitor
