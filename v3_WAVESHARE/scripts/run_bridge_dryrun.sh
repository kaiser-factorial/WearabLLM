#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V3_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BRIDGE_DIR="${V3_DIR}/bridge"

HOST="${WEARABLLM_BRIDGE_HOST:-0.0.0.0}"
PORT="${WEARABLLM_BRIDGE_PORT:-8765}"
CAPTURE_DIR="${WEARABLLM_CAPTURE_DIR:-${BRIDGE_DIR}/captures}"
DRY_RUN_COMMAND="${WEARABLLM_DRY_RUN_COMMAND:-BS}"
DRY_RUN_SEQUENCE="${WEARABLLM_DRY_RUN_SEQUENCE:-}"

cd "${BRIDGE_DIR}"

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install -r requirements.txt

exec python wearabllm_bridge.py \
    --host "${HOST}" \
    --port "${PORT}" \
    --dry-run \
    --dry-run-command "${DRY_RUN_COMMAND}" \
    --dry-run-sequence "${DRY_RUN_SEQUENCE}" \
    --allow-device-config \
    --save-wav-dir "${CAPTURE_DIR}"
