#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V3_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
FIRMWARE_DIR="${V3_DIR}/firmware"
IDF_PATH_DEFAULT="/Users/corinakaiser/Projects/wearabLLM/.toolchains/esp-idf-v5.5"
IDF_PATH="${IDF_PATH:-${IDF_PATH_DEFAULT}}"
CODEX_PYTHON_BIN="/Users/corinakaiser/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin"

usage() {
    cat <<'EOF'
Usage: ./scripts/firmware_variant_build.sh [variant ...]

Variants:
  default        Build the normal local firmware/sdkconfig.
  led-self-test  Compile with RGB ring self-test enabled.
  display        Compile with optional ST7735 TFT enabled.
  display-test   Compile with TFT enabled plus boot wiring self-test.
  audio-out      Compile with optional ES8311 speaker output enabled.
  tts            Compile with speaker output plus bridge TTS WAV playback enabled.
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
            variants+=(default led-self-test display display-test audio-out tts)
            ;;
        default|led-self-test|display|display-test|audio-out|tts)
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
