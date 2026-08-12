#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Prefer a modern Python if available; fall back to python3.
PYTHON_BIN="${WEARABLLM_PYTHON:-python3}"
exec "${PYTHON_BIN}" "${SCRIPT_DIR}/../transcript_viewer/server.py" "$@"
