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

if [ ! -f "${IDF_PATH}/export.sh" ]; then
    echo "ESP-IDF export.sh not found at: ${IDF_PATH}" >&2
    echo "Set IDF_PATH to your ESP-IDF checkout and retry." >&2
    exit 1
fi

cd "${FIRMWARE_DIR}"

if [ -d "${CODEX_PYTHON_BIN}" ]; then
    export PATH="${CODEX_PYTHON_BIN}:${PATH}"
fi

# shellcheck disable=SC1091
source "${IDF_PATH}/export.sh"

idf.py build
