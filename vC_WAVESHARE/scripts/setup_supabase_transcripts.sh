#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PROJECT_REF="${1:-}"

if [ -z "${PROJECT_REF}" ]; then
    echo "Usage: $0 <supabase-project-ref>" >&2
    exit 1
fi
if ! command -v supabase >/dev/null 2>&1; then
    echo "Supabase CLI is required." >&2
    exit 1
fi

umask 077
DEVICE_TOKEN="$(openssl rand -hex 32)"
SECRETS_FILE="$(mktemp "${TMPDIR:-/tmp}/wearabllm-supabase.XXXXXX")"
trap 'rm -f "${SECRETS_FILE}"' EXIT
printf 'WEARABLLM_DEVICE_TOKEN=%s\n' "${DEVICE_TOKEN}" > "${SECRETS_FILE}"

cd "${REPO_DIR}"
supabase link --project-ref "${PROJECT_REF}"
supabase db push --linked
supabase secrets set --project-ref "${PROJECT_REF}" --env-file "${SECRETS_FILE}"
supabase functions deploy wearabllm-transcript --project-ref "${PROJECT_REF}" --no-verify-jwt

WEARABLLM_TRANSCRIPT_LOG_URL="https://${PROJECT_REF}.supabase.co/functions/v1/wearabllm-transcript" \
WEARABLLM_TRANSCRIPT_DEVICE_TOKEN="${DEVICE_TOKEN}" \
python3 "${SCRIPT_DIR}/configure_firmware.py" --enable-transcript-log

echo
echo "Supabase transcript logging is deployed and staged in ignored firmware/sdkconfig."
echo "Build and perform the final USB flash:"
echo "  ./vC_WAVESHARE/scripts/firmware_build.sh"
echo "  ./vC_WAVESHARE/scripts/firmware_flash_monitor.sh"
