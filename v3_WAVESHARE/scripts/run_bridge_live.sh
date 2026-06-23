#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V3_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BRIDGE_DIR="${V3_DIR}/bridge"
HOST="${WEARABLLM_BRIDGE_HOST:-0.0.0.0}"
PORT="${WEARABLLM_BRIDGE_PORT:-8765}"
CAPTURE_DIR="${WEARABLLM_CAPTURE_DIR:-${BRIDGE_DIR}/captures}"
KEYCHAIN_SERVICE="${WEARABLLM_KEYCHAIN_SERVICE:-wearabllm-openai-api-key}"
KEYCHAIN_ACCOUNT="${WEARABLLM_KEYCHAIN_ACCOUNT:-${USER:-wearabllm}}"
MEMORY_ARGS=()
if [ "${WEARABLLM_DURABLE_MEMORY:-1}" = "1" ]; then
    MEMORY_ARGS+=(
        --durable-memory
        --memory-backend "${WEARABLLM_MEMORY_BACKEND:-mem}"
        --mem-root "${WEARABLLM_MEM_ROOT:-$HOME/Projects/MEMORY}"
    )
fi

cd "${BRIDGE_DIR}"

PYTHON_BOOTSTRAP="${WEARABLLM_PYTHON:-python3}"
if [ -x /usr/bin/python3 ] && [ -z "${WEARABLLM_PYTHON:-}" ]; then
    PYTHON_BOOTSTRAP=/usr/bin/python3
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
    "${MEMORY_ARGS[@]}" \
    --allow-device-config \
    --save-wav-dir "${CAPTURE_DIR}"
