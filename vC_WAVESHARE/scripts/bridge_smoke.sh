#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIGURED_BASE_URL="$(
    "${SCRIPT_DIR}/configure_firmware.py" --status-json 2>/dev/null | python3 -c '
import json
import sys
from urllib.parse import urlparse

try:
    payload = json.load(sys.stdin)
except json.JSONDecodeError:
    payload = {}
url = urlparse(payload.get("bridge_url") or "")
port = url.port or 8765
print(f"http://127.0.0.1:{port}")
' || printf 'http://127.0.0.1:8765'
)"
BASE_URL="${1:-${WEARABLLM_BRIDGE_BASE_URL:-${CONFIGURED_BASE_URL}}}"
BASE_URL="${BASE_URL%/}"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/wearabllm-bridge-smoke.XXXXXX")"
QUEUE_IDEMPOTENCY_KEY="bridge-smoke-queue-$(date +%s)-$$"

cleanup() {
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

echo "Bridge base URL: ${BASE_URL}"

curl -fsS "${BASE_URL}/health" -o "${TMP_DIR}/health.json"
BRIDGE_DRY_RUN="$(python3 - "${TMP_DIR}/health.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
assert payload.get("ok") is True, payload
assert payload.get("service") == "wearabllm-bridge", payload
config = payload.get("config", {})
print("health ok:", json.dumps(config, sort_keys=True))
print("dry_run=1" if config.get("dry_run") is True else "dry_run=0")
print(f"max_audio_bytes={int(config.get('max_audio_bytes') or 0)}")
PY
)"
echo "${BRIDGE_DRY_RUN}" | sed -n '/^health ok:/p'
DRY_RUN_FLAG="$(echo "${BRIDGE_DRY_RUN}" | sed -n 's/^dry_run=//p' | tail -n 1)"
MAX_AUDIO_BYTES="$(echo "${BRIDGE_DRY_RUN}" | sed -n 's/^max_audio_bytes=//p' | tail -n 1)"

python3 - "${TMP_DIR}/query.wav" <<'PY'
import struct
import sys
import wave
from pathlib import Path

path = Path(sys.argv[1])
with wave.open(str(path), "wb") as wav_file:
    wav_file.setnchannels(1)
    wav_file.setsampwidth(2)
    wav_file.setframerate(16000)
    frames = []
    for index in range(1600):
        sample = 1200 if index % 32 < 16 else -1200
        frames.append(struct.pack("<h", sample))
    wav_file.writeframes(b"".join(frames))
PY

if [ "${DRY_RUN_FLAG}" = "1" ] || [ "${WEARABLLM_SMOKE_AUDIO:-0}" = "1" ]; then
    curl -fsS \
        -H "Content-Type: audio/wav" \
        --data-binary @"${TMP_DIR}/query.wav" \
        "${BASE_URL}/v1/query" \
        -o "${TMP_DIR}/query_audio.json"
    python3 - "${TMP_DIR}/query_audio.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
assert payload.get("command") in {"GS", "GP", "GC", "RS", "RF", "YP", "BS", "PS", "PP"}, payload
assert isinstance(payload.get("reply"), str) and payload["reply"], payload
assert payload.get("audio_bytes", 0) > 44, payload
wav_info = payload.get("wav_info")
assert isinstance(wav_info, dict), payload
assert wav_info.get("valid") is True, wav_info
assert wav_info.get("sample_rate") == 16000, wav_info
assert wav_info.get("channels") == 1, wav_info
assert wav_info.get("appears_silent") is False, wav_info
print("query audio ok:", payload["command"], json.dumps(wav_info, sort_keys=True))
PY
    curl -fsS "${BASE_URL}/health" -o "${TMP_DIR}/health_after_audio.json"
    python3 - "${TMP_DIR}/health_after_audio.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
config = payload.get("config", {})
latest = config.get("latest_capture")
assert config.get("capture_count", 0) >= 1, config
assert isinstance(latest, dict), config
assert latest.get("audio_bytes", 0) > 44, latest
assert latest.get("command") in {"GS", "GP", "GC", "RS", "RF", "YP", "BS", "PS", "PP"}, latest
wav_info = latest.get("wav_info")
assert isinstance(wav_info, dict), latest
assert wav_info.get("valid") is True, wav_info
assert wav_info.get("appears_silent") is False, wav_info
print("health latest_capture ok:", latest["command"], latest["audio_bytes"])
PY
    if [ "${MAX_AUDIO_BYTES}" -gt 0 ] && [ "${MAX_AUDIO_BYTES}" -le 2097152 ]; then
        python3 - "${TMP_DIR}/oversized.bin" "${MAX_AUDIO_BYTES}" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
limit = int(sys.argv[2])
path.write_bytes(b"0" * (limit + 1))
PY
        HTTP_STATUS="$(
            curl -sS \
                -H "Content-Type: audio/wav" \
                --data-binary @"${TMP_DIR}/oversized.bin" \
                -o "${TMP_DIR}/oversized.json" \
                -w "%{http_code}" \
                "${BASE_URL}/v1/query"
        )"
        python3 - "${TMP_DIR}/oversized.json" "${HTTP_STATUS}" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
status = int(sys.argv[2])
assert status == 413, (status, payload)
assert "Audio body too large" in payload.get("error", ""), payload
print("query audio limit ok:", payload["error"])
PY
    else
        echo "query audio limit skipped: max_audio_bytes=${MAX_AUDIO_BYTES}"
    fi
else
    echo "query audio skipped: bridge is not in dry-run mode (set WEARABLLM_SMOKE_AUDIO=1 to force)"
fi

curl -fsS \
    -H "Content-Type: application/json" \
    -d '{"transcript":"bridge smoke test"}' \
    "${BASE_URL}/v1/query_text" \
    -o "${TMP_DIR}/query_text.json"
python3 - "${TMP_DIR}/query_text.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
assert payload.get("command") in {"GS", "GP", "GC", "RS", "RF", "YP", "BS", "PS", "PP"}, payload
assert isinstance(payload.get("reply"), str) and payload["reply"], payload
assert payload.get("transcript") == "bridge smoke test", payload
assert payload.get("audio_bytes") == 0, payload
assert payload.get("wav_info") is None, payload
print("query_text ok:", payload["command"], payload["reply"][:80])
PY

curl -fsS \
    -H "Content-Type: application/json" \
    -d "{\"transcript\":\"queue smoke test\",\"origin_device_id\":\"wearabllm-smoke\",\"target_device_id\":\"wearabllm-esp32\",\"idempotency_key\":\"${QUEUE_IDEMPOTENCY_KEY}\"}" \
    "${BASE_URL}/v1/interactions" \
    -o "${TMP_DIR}/interaction.json"
ACTION_ID="$(python3 - "${TMP_DIR}/interaction.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
action = payload.get("action")
assert isinstance(action, dict), payload
assert action.get("target_device_id") == "wearabllm-esp32", action
assert action.get("status") == "queued", action
assert isinstance(action.get("id"), str) and action["id"], action
print(action["id"])
PY
)"

curl -fsS \
    -H "X-WearabLLM-Device-Id: wearabllm-esp32" \
    "${BASE_URL}/v1/devices/wearabllm-esp32/actions" \
    -o "${TMP_DIR}/claimed_action.json"
python3 - "${TMP_DIR}/claimed_action.json" "${ACTION_ID}" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
action = payload.get("action")
assert isinstance(action, dict), payload
assert action.get("id") == sys.argv[2], action
assert action.get("status") == "dispatched", action
assert isinstance(action.get("reply"), str) and action["reply"], action
print("queued interaction claimed:", action["id"])
PY

curl -fsS \
    -H "Content-Type: application/json" \
    -H "X-WearabLLM-Device-Id: wearabllm-esp32" \
    -d '{"status":"played"}' \
    "${BASE_URL}/v1/devices/wearabllm-esp32/actions/${ACTION_ID}/ack" \
    -o "${TMP_DIR}/acknowledged_action.json"
python3 - "${TMP_DIR}/acknowledged_action.json" "${ACTION_ID}" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
action = payload.get("action")
assert isinstance(action, dict), payload
assert action.get("id") == sys.argv[2], action
assert action.get("status") == "played", action
print("queued interaction acknowledged:", action["status"])
PY

curl -fsS \
    -H "Content-Type: application/json" \
    -d '{"text":"bridge smoke test tts"}' \
    "${BASE_URL}/v1/tts" \
    -o "${TMP_DIR}/tts.wav"
python3 - "${TMP_DIR}/tts.wav" <<'PY'
import sys
import wave
from pathlib import Path

path = Path(sys.argv[1])
assert path.stat().st_size > 44, path.stat().st_size
with wave.open(str(path), "rb") as wav_file:
    channels = wav_file.getnchannels()
    rate = wav_file.getframerate()
    width = wav_file.getsampwidth()
    frames = wav_file.getnframes()
assert channels >= 1, channels
assert rate > 0, rate
assert width > 0, width
assert frames > 0, frames
print(f"tts wav ok: {rate} Hz, {channels} ch, {width} bytes/sample, {frames} frames")
PY

echo "Bridge smoke passed."
