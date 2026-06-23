#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V3_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BRIDGE_DIR="${V3_DIR}/bridge"

HOST="${WEARABLLM_BRIDGE_HOST:-0.0.0.0}"
CAPTURE_DIR="${WEARABLLM_CAPTURE_DIR:-${BRIDGE_DIR}/captures}"
DRY_RUN_COMMAND="${WEARABLLM_DRY_RUN_COMMAND:-BS}"
DRY_RUN_SEQUENCE="${WEARABLLM_DRY_RUN_SEQUENCE:-}"
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

cd "${BRIDGE_DIR}"

PYTHON_BIN="python3"
if [ -d ".venv" ] && [ ! -f ".venv/bin/activate" ]; then
    echo "warning: bridge .venv is incomplete; using system python3. Remove .venv to recreate it." >&2
elif [ ! -f ".venv/bin/activate" ]; then
    if ! python3 -m venv .venv; then
        rm -r .venv 2>/dev/null || true
        echo "warning: could not create bridge .venv; using system python3 for dry-run bridge" >&2
    fi
fi

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
    PYTHON_BIN="python"
    if ! python -m pip install -r requirements.txt; then
        echo "warning: pip install failed; continuing with current Python environment" >&2
    fi
fi

echo "WearabLLM dry-run bridge"
echo "  listen: http://${HOST}:${PORT}"
echo "  board target from firmware: ${CONFIGURED_BRIDGE_URL:-unknown}"
echo "  dry-run command: ${DRY_RUN_COMMAND}"
echo "  dry-run sequence: ${DRY_RUN_SEQUENCE:-none}"
echo "  capture dir: ${CAPTURE_DIR}"
echo "  device config endpoint: enabled"

exec "${PYTHON_BIN}" wearabllm_bridge.py \
    --host "${HOST}" \
    --port "${PORT}" \
    --dry-run \
    --dry-run-command "${DRY_RUN_COMMAND}" \
    --dry-run-sequence "${DRY_RUN_SEQUENCE}" \
    --allow-device-config \
    --save-wav-dir "${CAPTURE_DIR}"
