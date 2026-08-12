#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V3_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BRIDGE_DIR="${V3_DIR}/bridge"
HOST="${WEARABLLM_BRIDGE_HOST:-0.0.0.0}"
CAPTURE_DIR="${WEARABLLM_CAPTURE_DIR:-${BRIDGE_DIR}/captures}"
KEYCHAIN_SERVICE="${WEARABLLM_KEYCHAIN_SERVICE:-wearabllm-openai-api-key}"
KEYCHAIN_ACCOUNT="${WEARABLLM_KEYCHAIN_ACCOUNT:-${USER:-wearabllm}}"
FIRMWARE_STATUS="$("${SCRIPT_DIR}/configure_firmware.py" --status-json 2>/dev/null || true)"
CONFIGURED_BRIDGE_URL="$(
    python3 - "${FIRMWARE_STATUS}" <<'PY'
import json
import sys

try:
    payload = json.loads(sys.argv[1] or "{}")
except json.JSONDecodeError:
    payload = {}
print(payload.get("bridge_url") or "")
PY
)"
CONFIGURED_BRIDGE_PORT="$(
    python3 - "${CONFIGURED_BRIDGE_URL}" <<'PY'
import sys
from urllib.parse import urlparse

url = urlparse(sys.argv[1])
print(url.port or "")
PY
)"
PORT="${WEARABLLM_BRIDGE_PORT:-${CONFIGURED_BRIDGE_PORT:-8765}}"
MEMORY_ARGS=()
if [ "${WEARABLLM_DURABLE_MEMORY:-0}" = "1" ]; then
    MEMORY_ARGS+=(
        --durable-memory
        --memory-backend "${WEARABLLM_MEMORY_BACKEND:-supabase}"
    )
    if [ "${WEARABLLM_MEMORY_BACKEND:-supabase}" = "mem" ]; then
        MEMORY_ARGS+=(--mem-root "${WEARABLLM_MEM_ROOT:?set WEARABLLM_MEM_ROOT explicitly for the legacy mem adapter}")
    fi
fi

cd "${BRIDGE_DIR}"

PYTHON_BOOTSTRAP="${WEARABLLM_PYTHON:-python3}"
if [ -x /usr/bin/python3 ] && [ -z "${WEARABLLM_PYTHON:-}" ]; then
    PYTHON_BOOTSTRAP=/usr/bin/python3
fi

DEVICE_TOKEN="$(${PYTHON_BOOTSTRAP} - "${V3_DIR}/firmware/sdkconfig" <<'PY'
import ast
import sys
from pathlib import Path

path = Path(sys.argv[1])
prefix = "CONFIG_WEARABLLM_BRIDGE_AUTH_TOKEN="
if path.is_file():
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            value = ast.literal_eval(line[len(prefix):])
            if isinstance(value, str):
                print(value)
            break
PY
)"
AUTH_ARGS=()
if [ -n "${DEVICE_TOKEN}" ]; then
    AUTH_ARGS+=(--device-token "${DEVICE_TOKEN}")
fi

if [ -d ".venv" ] && [ ! -f ".venv/bin/activate" ]; then
    rm -rf .venv
fi

if [ ! -f ".venv/bin/activate" ]; then
    "${PYTHON_BOOTSTRAP}" -m venv .venv
fi

source .venv/bin/activate
python -m pip install -r requirements.txt

KEYCHAIN_API_KEY=""
if command -v security >/dev/null 2>&1; then
    KEYCHAIN_API_KEY="$(
        security find-generic-password \
            -a "${KEYCHAIN_ACCOUNT}" \
            -s "${KEYCHAIN_SERVICE}" \
            -w 2>/dev/null || true
    )"
fi

if [ -z "${OPENAI_API_KEY:-}" ] && [ -n "${KEYCHAIN_API_KEY}" ]; then
    OPENAI_API_KEY="${KEYCHAIN_API_KEY}"
    export OPENAI_API_KEY
fi

if [ -z "${OPENAI_API_KEY:-}" ]; then
    read -r -s -p "OpenAI API key (hidden): " OPENAI_API_KEY
    echo
    export OPENAI_API_KEY

fi

if [ -z "${OPENAI_API_KEY}" ]; then
    echo "OPENAI_API_KEY cannot be empty." >&2
    exit 1
fi

if command -v security >/dev/null 2>&1 && [ -z "${KEYCHAIN_API_KEY}" ]; then
    security add-generic-password \
        -U \
        -a "${KEYCHAIN_ACCOUNT}" \
        -s "${KEYCHAIN_SERVICE}" \
        -w "${OPENAI_API_KEY}" >/dev/null
    echo "OpenAI API key saved in macOS Keychain."
fi

echo "WearabLLM live bridge"
echo "  listen: http://${HOST}:${PORT}"
echo "  capture dir: ${CAPTURE_DIR}"

exec python wearabllm_bridge.py \
    --host "${HOST}" \
    --port "${PORT}" \
    "${AUTH_ARGS[@]}" \
    "${MEMORY_ARGS[@]}" \
    --allow-device-config \
    --save-wav-dir "${CAPTURE_DIR}"
