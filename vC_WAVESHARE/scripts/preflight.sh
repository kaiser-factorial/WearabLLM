#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V3_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${V3_DIR}/.." && pwd)"
BRIDGE_PORT="${WEARABLLM_PREFLIGHT_PORT:-8766}"
BRIDGE_LOG="$(mktemp "${TMPDIR:-/tmp}/wearabllm-preflight-bridge.XXXXXX.log")"
CAPTURE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/wearabllm-preflight-captures.XXXXXX")"
BRIDGE_PID=""
RUN_FIRMWARE=1
RUN_APP=1
RUN_SMOKE=1
PYTHON_BIN="${WEARABLLM_PYTHON:-python3}"

if ! "${PYTHON_BIN}" -c 'import audioop' >/dev/null 2>&1; then
    for candidate in /usr/bin/python3 \
        "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"; do
        if [ -x "${candidate}" ] && "${candidate}" -c 'import audioop' >/dev/null 2>&1; then
            PYTHON_BIN="${candidate}"
            break
        fi
    done
fi

if ! "${PYTHON_BIN}" -c 'import audioop' >/dev/null 2>&1; then
    echo "No Python runtime with audioop support was found." >&2
    echo "Install vC_WAVESHARE/bridge/requirements.txt or set WEARABLLM_PYTHON." >&2
    exit 1
fi

usage() {
    cat <<'EOF'
Usage: ./scripts/preflight.sh [options]

Runs the v3 bench software checks:
  - Python compile checks for helper scripts and bridge
  - bridge unit tests
  - app protocol test and TypeScript typecheck
  - dry-run bridge smoke, including /v1/query audio/wav
  - firmware build

Options:
  --skip-firmware   Skip ESP-IDF firmware build.
  --skip-app        Skip Expo app checks.
  --skip-smoke      Skip local bridge dry-run smoke.
  -h, --help        Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --skip-firmware)
            RUN_FIRMWARE=0
            ;;
        --skip-app)
            RUN_APP=0
            ;;
        --skip-smoke)
            RUN_SMOKE=0
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

cleanup() {
    if [ -n "${BRIDGE_PID}" ] && kill -0 "${BRIDGE_PID}" 2>/dev/null; then
        kill "${BRIDGE_PID}" 2>/dev/null || true
        wait "${BRIDGE_PID}" 2>/dev/null || true
    fi
    rm -rf "${CAPTURE_DIR}" "${BRIDGE_LOG}"
}
trap cleanup EXIT

step() {
    printf '\n==> %s\n' "$1"
}

wait_for_bridge() {
    local url="http://127.0.0.1:${BRIDGE_PORT}/health"
    for _ in $(seq 1 60); do
        if curl -fsS "${url}" >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.25
    done
    echo "Bridge did not become ready. Log:" >&2
    cat "${BRIDGE_LOG}" >&2 || true
    return 1
}

cd "${REPO_ROOT}"

step "Python compile checks"
"${PYTHON_BIN}" -m py_compile \
    vC_WAVESHARE/scripts/analyze_serial_log.py \
    vC_WAVESHARE/scripts/bench_doctor.py \
    vC_WAVESHARE/scripts/bench_report.py \
    vC_WAVESHARE/scripts/bringup_info.py \
    vC_WAVESHARE/scripts/configure_firmware.py \
    vC_WAVESHARE/scripts/inspect_captures.py \
    vC_WAVESHARE/scripts/validate_protocol.py \
    vC_WAVESHARE/scripts/verify_firmware_image.py \
    vC_WAVESHARE/bridge/wearabllm_bridge.py

step "Protocol consistency"
"${PYTHON_BIN}" vC_WAVESHARE/scripts/validate_protocol.py

bash -n \
    vC_WAVESHARE/scripts/bridge_smoke.sh \
    vC_WAVESHARE/scripts/firmware_build.sh \
    vC_WAVESHARE/scripts/firmware_flash_monitor.sh \
    vC_WAVESHARE/scripts/preflight.sh \
    vC_WAVESHARE/scripts/run_bridge_dryrun.sh \
    vC_WAVESHARE/scripts/serial_capture.sh

step "Bridge unit tests"
"${PYTHON_BIN}" -m unittest discover -s vC_WAVESHARE/bridge -p 'test_*.py'

step "Bench helper unit tests"
"${PYTHON_BIN}" -m unittest discover -s vC_WAVESHARE/scripts -p 'test_*.py'

if [ "${RUN_APP}" = "1" ]; then
    step "App protocol and type checks"
    cd "${V3_DIR}/app"
    if [ ! -d "node_modules" ]; then
        npm ci
    fi
    npm run test:protocol
    npm run typecheck
    cd "${REPO_ROOT}"
fi

if [ "${RUN_SMOKE}" = "1" ]; then
    step "Dry-run bridge smoke on 127.0.0.1:${BRIDGE_PORT}"
    cd "${V3_DIR}/bridge"
    WEARABLLM_DRY_RUN_SEQUENCE=GS,RF \
        "${PYTHON_BIN}" wearabllm_bridge.py \
            --host 127.0.0.1 \
            --port "${BRIDGE_PORT}" \
            --dry-run \
            --dry-run-command PP \
            --save-wav-dir "${CAPTURE_DIR}" \
            >"${BRIDGE_LOG}" 2>&1 &
    BRIDGE_PID="$!"
    cd "${V3_DIR}"
    wait_for_bridge
    ./scripts/bridge_smoke.sh "http://127.0.0.1:${BRIDGE_PORT}"
    cd "${REPO_ROOT}"
fi

if [ "${RUN_FIRMWARE}" = "1" ]; then
    step "Firmware build"
    cd "${V3_DIR}"
    ./scripts/firmware_build.sh
    cd "${REPO_ROOT}"
fi

step "Preflight passed"
