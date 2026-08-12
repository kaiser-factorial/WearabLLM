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

usage() {
    cat <<'EOF'
Usage: ./scripts/firmware_variant_build.sh [variant ...]

Variants:
  default        Build the normal local firmware/sdkconfig.
  led-self-test  Compile with RGB ring self-test enabled.
  display        Compile with optional ST7789 TFT200C enabled.
  display-test   Compile with TFT enabled plus boot wiring self-test.
  audio-out      Compile with optional ES8311 speaker output enabled.
  tts            Compile with speaker output plus bridge TTS WAV playback enabled.
  direct-openai  Compile the bridge-free path with a non-secret placeholder key.
  transcript-log Compile the background HTTPS transcript uploader.
  all            Build every variant above.
EOF
}

if [ ! -f "${IDF_PATH}/export.sh" ]; then
    echo "ESP-IDF export.sh not found at: ${IDF_PATH}" >&2
    echo "Set IDF_PATH to your ESP-IDF checkout and retry." >&2
    exit 1
fi

if [ "$#" -eq 0 ]; then
    set -- default display display-test audio-out tts
fi

variants=()
for variant in "$@"; do
    case "${variant}" in
        -h|--help)
            usage
            exit 0
            ;;
        all)
            variants+=(default led-self-test display display-test audio-out tts direct-openai transcript-log)
            ;;
        default|led-self-test|display|display-test|audio-out|tts|direct-openai|transcript-log)
            variants+=("${variant}")
            ;;
        *)
            echo "Unknown variant: ${variant}" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [ -d "${CODEX_PYTHON_BIN}" ]; then
    export PATH="${CODEX_PYTHON_BIN}:${PATH}"
fi

# shellcheck disable=SC1091
source "${IDF_PATH}/export.sh"

make_defaults() {
    local variant="$1"
    local defaults_file="$2"

    cp "${FIRMWARE_DIR}/sdkconfig.defaults" "${defaults_file}"
    {
        echo ""
        case "${variant}" in
            led-self-test)
                echo "CONFIG_WEARABLLM_LED_SELF_TEST_ON_BOOT=y"
                ;;
            display)
                echo "CONFIG_WEARABLLM_DISPLAY_ENABLED=y"
                ;;
            display-test)
                echo "CONFIG_WEARABLLM_DISPLAY_ENABLED=y"
                echo "CONFIG_WEARABLLM_DISPLAY_SELF_TEST_ON_BOOT=y"
                ;;
            audio-out)
                echo "CONFIG_WEARABLLM_AUDIO_OUT_ENABLED=y"
                echo "CONFIG_WEARABLLM_AUDIO_OUT_VOLUME=45"
                ;;
            tts)
                echo "CONFIG_WEARABLLM_AUDIO_OUT_ENABLED=y"
                echo "CONFIG_WEARABLLM_AUDIO_OUT_VOLUME=45"
                echo "CONFIG_WEARABLLM_TTS_ENABLED=y"
                echo "CONFIG_WEARABLLM_TTS_MAX_BYTES=131072"
                ;;
            direct-openai)
                echo "CONFIG_WEARABLLM_DIRECT_OPENAI=y"
                echo 'CONFIG_WEARABLLM_OPENAI_API_KEY="placeholder-build-only"'
                echo "CONFIG_WEARABLLM_AUDIO_OUT_ENABLED=y"
                ;;
            transcript-log)
                echo "CONFIG_WEARABLLM_TRANSCRIPT_LOG_ENABLED=y"
                echo 'CONFIG_WEARABLLM_TRANSCRIPT_LOG_URL="https://example.supabase.co/functions/v1/wearabllm-transcript"'
                echo 'CONFIG_WEARABLLM_TRANSCRIPT_DEVICE_TOKEN="placeholder-build-only"'
                ;;
        esac
    } >> "${defaults_file}"
}

build_variant() {
    local variant="$1"

    cd "${FIRMWARE_DIR}"
    if [ "${variant}" = "default" ]; then
        echo "==> Building firmware variant: default"
        idf.py build
        return
    fi

    local build_dir="build_${variant//-/_}_on"
    local tmp_dir
    tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/wearabllm-${variant}.XXXXXX")"
    trap 'rm -rf "${tmp_dir}"' RETURN
    local defaults_file="${tmp_dir}/sdkconfig.defaults"
    local sdkconfig_file="${tmp_dir}/sdkconfig"
    make_defaults "${variant}" "${defaults_file}"

    echo "==> Building firmware variant: ${variant}"
    idf.py \
        -B "${build_dir}" \
        -D "SDKCONFIG=${sdkconfig_file}" \
        -D "SDKCONFIG_DEFAULTS=${defaults_file}" \
        build
}

for variant in "${variants[@]}"; do
    build_variant "${variant}"
done
