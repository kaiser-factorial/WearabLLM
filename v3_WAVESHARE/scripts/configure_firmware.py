#!/usr/bin/env python3
"""Update local ESP-IDF sdkconfig values for WearabLLM v3 bench tests."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

from bringup_info import candidate_ipv4_addresses

SCRIPT_DIR = Path(__file__).resolve().parent
V3_DIR = SCRIPT_DIR.parent
FIRMWARE_DIR = V3_DIR / "firmware"
BSSID_PATTERN = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")


def kconfig_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def set_kconfig_value(lines: list[str], key: str, value: str) -> list[str]:
    prefix = f"CONFIG_{key}="
    replacement = f"{prefix}{value}"
    updated = False
    out: list[str] = []
    for line in lines:
        if line.startswith(prefix):
            out.append(replacement)
            updated = True
        else:
            out.append(line)
    if not updated:
        out.append(replacement)
    return out


def set_kconfig_bool(lines: list[str], key: str, enabled: bool) -> list[str]:
    prefix = f"CONFIG_{key}="
    disabled = f"# CONFIG_{key} is not set"
    replacement = f"CONFIG_{key}=y" if enabled else disabled
    updated = False
    out: list[str] = []
    for line in lines:
        if line.startswith(prefix) or line == disabled:
            out.append(replacement)
            updated = True
        else:
            out.append(line)
    if not updated:
        out.append(replacement)
    return out


def set_kconfig_choice(lines: list[str], keys: list[str], enabled_key: str) -> list[str]:
    if enabled_key not in keys:
        raise ValueError(f"{enabled_key} is not a valid choice")

    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        replaced = False
        for key in keys:
            if line.startswith(f"CONFIG_{key}=") or line == f"# CONFIG_{key} is not set":
                seen.add(key)
                if key == enabled_key:
                    out.append(f"CONFIG_{key}=y")
                else:
                    out.append(f"# CONFIG_{key} is not set")
                replaced = True
                break
        if not replaced:
            out.append(line)

    for key in keys:
        if key not in seen:
            if key == enabled_key:
                out.append(f"CONFIG_{key}=y")
            else:
                out.append(f"# CONFIG_{key} is not set")
    return out


def parse_kconfig_string(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        raw = raw[1:-1]
    return raw.replace('\\"', '"').replace("\\\\", "\\")


def get_kconfig_value(lines: list[str], key: str) -> str | None:
    prefix = f"CONFIG_{key}="
    for line in lines:
        if line.startswith(prefix):
            return line[len(prefix):]
    return None


def masked(value: str) -> str:
    if not value:
        return "(empty)"
    return "(set)"


def normalize_bssid(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if not BSSID_PATTERN.fullmatch(value):
        raise SystemExit("Wi-Fi BSSID must look like 02:00:00:00:00:01")
    return value.lower()


def ptt_pull_choice(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    choices = {
        "none": "WEARABLLM_PTT_PULL_NONE",
        "off": "WEARABLLM_PTT_PULL_NONE",
        "pull-none": "WEARABLLM_PTT_PULL_NONE",
        "up": "WEARABLLM_PTT_PULL_UP",
        "pull-up": "WEARABLLM_PTT_PULL_UP",
        "down": "WEARABLLM_PTT_PULL_DOWN",
        "pull-down": "WEARABLLM_PTT_PULL_DOWN",
    }
    if normalized not in choices:
        raise SystemExit("--ptt-pull must be one of: none, up, down")
    return choices[normalized]


def ptt_pull_status(lines: list[str]) -> str:
    if get_kconfig_value(lines, "WEARABLLM_PTT_PULL_DOWN") == "y":
        return "down"
    if get_kconfig_value(lines, "WEARABLLM_PTT_PULL_NONE") == "y":
        return "none"
    return "up"


def kconfig_bool_status(lines: list[str], key: str) -> bool:
    return get_kconfig_value(lines, key) == "y"


def default_bridge_host() -> str:
    addresses = candidate_ipv4_addresses()
    return addresses[0] if addresses else "192.168.1.10"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Patch firmware/sdkconfig with local Wi-Fi and bridge values. "
            "sdkconfig is ignored by git; do not commit Wi-Fi secrets."
        )
    )
    parser.add_argument("--ssid", default=os.environ.get("WEARABLLM_WIFI_SSID", ""))
    parser.add_argument(
        "--password",
        default=os.environ.get("WEARABLLM_WIFI_PASSWORD", ""),
        help="Wi-Fi password. Prefer WEARABLLM_WIFI_PASSWORD to avoid shell history.",
    )
    parser.add_argument(
        "--bssid",
        default=os.environ.get("WEARABLLM_WIFI_BSSID", ""),
        help="Optional AP MAC/BSSID, for example 02:00:00:00:00:01.",
    )
    parser.add_argument(
        "--clear-bssid",
        action="store_true",
        help="Clear the optional AP MAC/BSSID pin and discover the AP by SSID.",
    )
    parser.add_argument("--bridge-host", default=os.environ.get("WEARABLLM_BRIDGE_HOST", ""))
    parser.add_argument(
        "--bridge-port",
        type=int,
        default=int(os.environ.get("WEARABLLM_BRIDGE_PORT", "8765")),
    )
    parser.add_argument(
        "--bridge-url",
        default=os.environ.get("WEARABLLM_BRIDGE_URL", ""),
        help="Full /v1/query URL. Overrides --bridge-host/--bridge-port.",
    )
    parser.add_argument(
        "--bridge-auth-token",
        default=os.environ.get("WEARABLLM_BRIDGE_AUTH_TOKEN", ""),
        help="Hosted bridge device token. Prefer WEARABLLM_BRIDGE_AUTH_TOKEN to avoid shell history.",
    )
    parser.add_argument(
        "--device-id",
        default=os.environ.get("WEARABLLM_DEVICE_ID", ""),
        help="Stable non-secret device ID sent to the hosted bridge, for example wearabllm-esp32.",
    )
    parser.add_argument(
        "--tts-url",
        default=os.environ.get("WEARABLLM_TTS_URL", ""),
        help="Full /v1/tts URL. Defaults to the same host/port as the bridge URL.",
    )
    direct_group = parser.add_mutually_exclusive_group()
    direct_group.add_argument("--enable-direct-openai", action="store_true")
    direct_group.add_argument("--disable-direct-openai", action="store_true")
    parser.add_argument(
        "--openai-api-key",
        default=os.environ.get("WEARABLLM_OPENAI_API_KEY", ""),
        help="Dedicated project key. Prefer WEARABLLM_OPENAI_API_KEY to avoid shell history.",
    )
    parser.add_argument(
        "--clear-openai-api-key",
        action="store_true",
        help="Remove the embedded direct-mode OpenAI key when switching to a hosted bridge.",
    )
    transcript_group = parser.add_mutually_exclusive_group()
    transcript_group.add_argument("--enable-transcript-log", action="store_true")
    transcript_group.add_argument("--disable-transcript-log", action="store_true")
    parser.add_argument(
        "--transcript-log-url",
        default=os.environ.get("WEARABLLM_TRANSCRIPT_LOG_URL", ""),
        help="HTTPS Edge Function URL that receives transcript events.",
    )
    parser.add_argument(
        "--transcript-device-id",
        default=os.environ.get("WEARABLLM_TRANSCRIPT_DEVICE_ID", ""),
    )
    parser.add_argument(
        "--transcript-device-token",
        default=os.environ.get("WEARABLLM_TRANSCRIPT_DEVICE_TOKEN", ""),
        help="Shared device token. Prefer the environment variable to avoid shell history.",
    )
    parser.add_argument("--ptt-gpio", type=int, default=None)
    parser.add_argument("--ptt-active-level", type=int, choices=(0, 1), default=None)
    parser.add_argument("--ptt-debounce-ms", type=int, default=None)
    parser.add_argument(
        "--ptt-pull",
        choices=("none", "up", "down"),
        default="",
        help="Internal PTT GPIO pull mode. Use up for GPIO-to-GND buttons, down for GPIO-to-3V3 buttons.",
    )
    parser.add_argument("--wifi-timeout-ms", type=int, default=None)
    parser.add_argument("--audio-min-capture-ms", type=int, default=None)
    parser.add_argument("--audio-max-seconds", type=int, default=None)
    audio_out_group = parser.add_mutually_exclusive_group()
    audio_out_group.add_argument(
        "--enable-audio-out",
        action="store_true",
        help="Enable ES8311 speaker output for the next firmware flash.",
    )
    audio_out_group.add_argument(
        "--disable-audio-out",
        action="store_true",
        help="Disable ES8311 speaker output for the next firmware flash.",
    )
    parser.add_argument(
        "--audio-out-volume",
        type=int,
        default=None,
        help="ES8311 speaker output volume, 0-100.",
    )
    tts_group = parser.add_mutually_exclusive_group()
    tts_group.add_argument(
        "--enable-tts",
        action="store_true",
        help="Enable bridge TTS WAV fetch/playback for the next firmware flash. Also enables audio output.",
    )
    tts_group.add_argument(
        "--disable-tts",
        action="store_true",
        help="Disable bridge TTS WAV fetch/playback for the next firmware flash.",
    )
    parser.add_argument(
        "--tts-max-bytes",
        type=int,
        default=None,
        help="Maximum TTS WAV response bytes buffered by firmware.",
    )
    led_test_group = parser.add_mutually_exclusive_group()
    led_test_group.add_argument(
        "--enable-led-self-test",
        action="store_true",
        help="Run the RGB ring command self-test at boot.",
    )
    led_test_group.add_argument(
        "--disable-led-self-test",
        action="store_true",
        help="Disable the RGB ring command self-test at boot.",
    )
    display_group = parser.add_mutually_exclusive_group()
    display_group.add_argument(
        "--enable-display",
        action="store_true",
        help="Enable the optional SPI TFT display for the next firmware flash.",
    )
    display_group.add_argument(
        "--disable-display",
        action="store_true",
        help="Disable the optional SPI TFT display for the next firmware flash.",
    )
    display_test_group = parser.add_mutually_exclusive_group()
    display_test_group.add_argument(
        "--enable-display-self-test",
        action="store_true",
        help="Run the TFT color/text wiring self-test at boot. Also enables the display.",
    )
    display_test_group.add_argument(
        "--disable-display-self-test",
        action="store_true",
        help="Disable the TFT boot wiring self-test.",
    )
    parser.add_argument(
        "--allow-partial-wifi",
        action="store_true",
        help="Allow writing only SSID or only password. Usually not wanted.",
    )
    parser.add_argument(
        "--sdkconfig",
        type=Path,
        default=FIRMWARE_DIR / "sdkconfig",
        help="Path to sdkconfig to update.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print values without writing.")
    parser.add_argument(
        "--status",
        action="store_true",
        help="Read current sdkconfig and report whether Wi-Fi/bridge settings are ready.",
    )
    parser.add_argument(
        "--status-json",
        action="store_true",
        help="Read current sdkconfig and print machine-readable status JSON.",
    )
    return parser.parse_args()


def status_payload(sdkconfig: Path, lines: list[str]) -> dict[str, object]:
    ssid = parse_kconfig_string(get_kconfig_value(lines, "WEARABLLM_WIFI_SSID") or '""')
    password = parse_kconfig_string(get_kconfig_value(lines, "WEARABLLM_WIFI_PASSWORD") or '""')
    bssid = parse_kconfig_string(get_kconfig_value(lines, "WEARABLLM_WIFI_BSSID") or '""')
    bridge_url = parse_kconfig_string(get_kconfig_value(lines, "WEARABLLM_BRIDGE_URL") or '""')
    bridge_auth_token = parse_kconfig_string(
        get_kconfig_value(lines, "WEARABLLM_BRIDGE_AUTH_TOKEN") or '""'
    )
    device_id = parse_kconfig_string(
        get_kconfig_value(lines, "WEARABLLM_DEVICE_ID") or '"wearabllm-esp32"'
    )
    direct_openai = kconfig_bool_status(lines, "WEARABLLM_DIRECT_OPENAI")
    openai_key = parse_kconfig_string(get_kconfig_value(lines, "WEARABLLM_OPENAI_API_KEY") or '""')
    transcript_log_enabled = kconfig_bool_status(lines, "WEARABLLM_TRANSCRIPT_LOG_ENABLED")
    transcript_log_url = parse_kconfig_string(
        get_kconfig_value(lines, "WEARABLLM_TRANSCRIPT_LOG_URL") or '""'
    )
    transcript_device_id = parse_kconfig_string(
        get_kconfig_value(lines, "WEARABLLM_TRANSCRIPT_DEVICE_ID") or '"wearabllm-esp32"'
    )
    transcript_device_token = parse_kconfig_string(
        get_kconfig_value(lines, "WEARABLLM_TRANSCRIPT_DEVICE_TOKEN") or '""'
    )
    ptt_gpio = get_kconfig_value(lines, "WEARABLLM_PTT_GPIO") or "(unset)"
    ptt_active_level = get_kconfig_value(lines, "WEARABLLM_PTT_ACTIVE_LEVEL") or "0"
    ptt_debounce_ms = get_kconfig_value(lines, "WEARABLLM_PTT_DEBOUNCE_MS") or "35"
    ptt_pull = ptt_pull_status(lines)
    timeout_ms = get_kconfig_value(lines, "WEARABLLM_WIFI_CONNECT_TIMEOUT_MS") or "(unset)"
    audio_min_ms = get_kconfig_value(lines, "WEARABLLM_AUDIO_MIN_CAPTURE_MS") or "250"
    audio_max_s = get_kconfig_value(lines, "WEARABLLM_AUDIO_MAX_SECONDS") or "6"
    audio_out_enabled = kconfig_bool_status(lines, "WEARABLLM_AUDIO_OUT_ENABLED")
    audio_out_volume = get_kconfig_value(lines, "WEARABLLM_AUDIO_OUT_VOLUME") or "45"
    tts_enabled = kconfig_bool_status(lines, "WEARABLLM_TTS_ENABLED")
    tts_url = parse_kconfig_string(get_kconfig_value(lines, "WEARABLLM_TTS_URL") or '""')
    tts_max_bytes = get_kconfig_value(lines, "WEARABLLM_TTS_MAX_BYTES") or "131072"
    led_self_test = kconfig_bool_status(lines, "WEARABLLM_LED_SELF_TEST_ON_BOOT")
    display_enabled = kconfig_bool_status(lines, "WEARABLLM_DISPLAY_ENABLED")
    display_self_test = kconfig_bool_status(lines, "WEARABLLM_DISPLAY_SELF_TEST_ON_BOOT")

    wifi_ready = bool(ssid and password)
    bridge_ready = bridge_url.startswith("http://") or bridge_url.startswith("https://")
    ready = wifi_ready and ((direct_openai and bool(openai_key)) or (not direct_openai and bridge_ready))

    return {
        "sdkconfig": str(sdkconfig),
        "wifi_ssid_set": bool(ssid),
        "wifi_password_set": bool(password),
        "wifi_bssid": bssid or None,
        "bridge_url": bridge_url or None,
        "bridge_auth_token_set": bool(bridge_auth_token),
        "device_id": device_id,
        "direct_openai": direct_openai,
        "openai_api_key_set": bool(openai_key),
        "transcript_log_enabled": transcript_log_enabled,
        "transcript_log_url": transcript_log_url or None,
        "transcript_device_id": transcript_device_id,
        "transcript_device_token_set": bool(transcript_device_token),
        "ptt_gpio": None if ptt_gpio == "(unset)" else int(ptt_gpio),
        "ptt_active_level": int(ptt_active_level),
        "ptt_debounce_ms": int(ptt_debounce_ms),
        "ptt_pull": ptt_pull,
        "wifi_timeout_ms": None if timeout_ms == "(unset)" else int(timeout_ms),
        "audio_min_capture_ms": int(audio_min_ms),
        "audio_max_seconds": int(audio_max_s),
        "audio_out_enabled": audio_out_enabled,
        "audio_out_volume": int(audio_out_volume),
        "tts_enabled": tts_enabled,
        "tts_url": tts_url or None,
        "tts_max_bytes": int(tts_max_bytes),
        "led_self_test": led_self_test,
        "display_enabled": display_enabled,
        "display_self_test": display_self_test,
        "ready": ready,
        "next": [
            *([] if wifi_ready else ["set both WEARABLLM_WIFI_SSID and WEARABLLM_WIFI_PASSWORD"]),
            *([] if direct_openai or bridge_ready else ["set bridge URL with --bridge-host or --bridge-url"]),
            *([] if direct_openai or not bridge_url.startswith("https://") or bridge_auth_token else [
                "set WEARABLLM_BRIDGE_AUTH_TOKEN for the hosted HTTPS bridge"
            ]),
            *([] if not direct_openai or openai_key else ["set WEARABLLM_OPENAI_API_KEY"]),
            *([] if not transcript_log_enabled or transcript_log_url else ["set WEARABLLM_TRANSCRIPT_LOG_URL"]),
            *([] if not transcript_log_enabled or transcript_device_token else ["set WEARABLLM_TRANSCRIPT_DEVICE_TOKEN"]),
        ],
    }


def print_status(sdkconfig: Path, lines: list[str]) -> int:
    status = status_payload(sdkconfig, lines)

    print("Firmware local config status:")
    print(f"  sdkconfig: {sdkconfig}")
    print(f"  Wi-Fi SSID: {'(set)' if status['wifi_ssid_set'] else '(empty)'}")
    print(f"  Wi-Fi password: {'(set)' if status['wifi_password_set'] else '(empty)'}")
    print(f"  Wi-Fi BSSID: {status['wifi_bssid'] or '(not pinned)'}")
    print(f"  bridge URL: {status['bridge_url'] or '(empty)'}")
    print(f"  bridge device token: {'(set)' if status['bridge_auth_token_set'] else '(empty)'}")
    print(f"  device ID: {status['device_id']}")
    print(f"  direct OpenAI: {'yes' if status['direct_openai'] else 'no'}")
    print(f"  OpenAI API key: {'(set)' if status['openai_api_key_set'] else '(empty)'}")
    print(f"  transcript logging: {'yes' if status['transcript_log_enabled'] else 'no'}")
    print(f"  transcript log URL: {status['transcript_log_url'] or '(empty)'}")
    print(f"  transcript device ID: {status['transcript_device_id']}")
    print(f"  transcript device token: {'(set)' if status['transcript_device_token_set'] else '(empty)'}")
    print(f"  PTT GPIO: {status['ptt_gpio'] if status['ptt_gpio'] is not None else '(unset)'}")
    print(f"  PTT active level: {status['ptt_active_level']}")
    print(f"  PTT debounce ms: {status['ptt_debounce_ms']}")
    print(f"  PTT pull: {status['ptt_pull']}")
    print(f"  Wi-Fi timeout ms: {status['wifi_timeout_ms'] if status['wifi_timeout_ms'] is not None else '(unset)'}")
    print(f"  audio min capture ms: {status['audio_min_capture_ms']}")
    print(f"  audio max seconds: {status['audio_max_seconds']}")
    print(f"  speaker output enabled: {'yes' if status['audio_out_enabled'] else 'no'}")
    print(f"  speaker output volume: {status['audio_out_volume']}")
    print(f"  TTS playback enabled: {'yes' if status['tts_enabled'] else 'no'}")
    print(f"  TTS URL: {status['tts_url'] or '(empty)'}")
    print(f"  TTS max bytes: {status['tts_max_bytes']}")
    print(f"  RGB ring boot self-test: {'yes' if status['led_self_test'] else 'no'}")
    print(f"  TFT display enabled: {'yes' if status['display_enabled'] else 'no'}")
    print(f"  TFT boot self-test: {'yes' if status['display_self_test'] else 'no'}")
    print(f"  ready for board-to-bridge dry-run test: {'yes' if status['ready'] else 'no'}")
    for next_step in status["next"]:
        print(f"  next: {next_step}")
    return 0 if status["ready"] else 1


def main() -> int:
    args = parse_args()
    if args.clear_openai_api_key and args.openai_api_key:
        raise SystemExit("--clear-openai-api-key cannot be combined with --openai-api-key")
    bssid = normalize_bssid(args.bssid)

    if bool(args.ssid) != bool(args.password) and not args.allow_partial_wifi:
        raise SystemExit(
            "Provide both --ssid and --password, set both WEARABLLM_WIFI_* env vars, "
            "or use --allow-partial-wifi."
        )

    sdkconfig = args.sdkconfig
    if sdkconfig.exists():
        lines = sdkconfig.read_text().splitlines()
    else:
        defaults = FIRMWARE_DIR / "sdkconfig.defaults"
        lines = defaults.read_text().splitlines() if defaults.exists() else []

    current = status_payload(sdkconfig, lines)
    bridge_args_present = bool(
        args.bridge_url
        or args.bridge_host
        or "--bridge-port" in sys.argv
        or os.environ.get("WEARABLLM_BRIDGE_PORT")
    )
    if bridge_args_present:
        bridge_host = args.bridge_host or default_bridge_host()
        bridge_url = args.bridge_url or f"http://{bridge_host}:{args.bridge_port}/v1/query"
        tts_url = args.tts_url or f"http://{bridge_host}:{args.bridge_port}/v1/tts"
    else:
        bridge_url = str(current.get("bridge_url") or "")
        tts_url = str(current.get("tts_url") or "")

    if args.status_json:
        print(json.dumps(status_payload(sdkconfig, lines), sort_keys=True))
        return 0

    if args.status:
        return print_status(sdkconfig, lines)

    updates = {
        "WEARABLLM_BRIDGE_URL": kconfig_quote(bridge_url),
        "WEARABLLM_TTS_URL": kconfig_quote(tts_url),
    }
    if args.openai_api_key:
        updates["WEARABLLM_OPENAI_API_KEY"] = kconfig_quote(args.openai_api_key)
    if args.clear_openai_api_key:
        updates["WEARABLLM_OPENAI_API_KEY"] = kconfig_quote("")
    if args.bridge_auth_token:
        updates["WEARABLLM_BRIDGE_AUTH_TOKEN"] = kconfig_quote(args.bridge_auth_token)
    if args.device_id:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", args.device_id):
            raise SystemExit("--device-id must contain only letters, numbers, dot, underscore, or hyphen")
        updates["WEARABLLM_DEVICE_ID"] = kconfig_quote(args.device_id)
    if args.transcript_log_url:
        if not args.transcript_log_url.startswith("https://"):
            raise SystemExit("--transcript-log-url must use https://")
        updates["WEARABLLM_TRANSCRIPT_LOG_URL"] = kconfig_quote(args.transcript_log_url)
    if args.transcript_device_id:
        updates["WEARABLLM_TRANSCRIPT_DEVICE_ID"] = kconfig_quote(args.transcript_device_id)
    if args.transcript_device_token:
        updates["WEARABLLM_TRANSCRIPT_DEVICE_TOKEN"] = kconfig_quote(args.transcript_device_token)
    if args.ssid:
        updates["WEARABLLM_WIFI_SSID"] = kconfig_quote(args.ssid)
    if args.password:
        updates["WEARABLLM_WIFI_PASSWORD"] = kconfig_quote(args.password)
    if args.clear_bssid:
        updates["WEARABLLM_WIFI_BSSID"] = kconfig_quote("")
    elif bssid:
        updates["WEARABLLM_WIFI_BSSID"] = kconfig_quote(bssid)
    if args.ptt_gpio is not None:
        updates["WEARABLLM_PTT_GPIO"] = str(args.ptt_gpio)
    if args.ptt_active_level is not None:
        updates["WEARABLLM_PTT_ACTIVE_LEVEL"] = str(args.ptt_active_level)
    if args.ptt_debounce_ms is not None:
        if args.ptt_debounce_ms < 0 or args.ptt_debounce_ms > 250:
            raise SystemExit("--ptt-debounce-ms must be between 0 and 250")
        updates["WEARABLLM_PTT_DEBOUNCE_MS"] = str(args.ptt_debounce_ms)
    if args.wifi_timeout_ms is not None:
        updates["WEARABLLM_WIFI_CONNECT_TIMEOUT_MS"] = str(args.wifi_timeout_ms)
    if args.audio_min_capture_ms is not None:
        if args.audio_min_capture_ms < 0 or args.audio_min_capture_ms > 2000:
            raise SystemExit("--audio-min-capture-ms must be between 0 and 2000")
        updates["WEARABLLM_AUDIO_MIN_CAPTURE_MS"] = str(args.audio_min_capture_ms)
    if args.audio_max_seconds is not None:
        if args.audio_max_seconds < 1 or args.audio_max_seconds > 20:
            raise SystemExit("--audio-max-seconds must be between 1 and 20")
        updates["WEARABLLM_AUDIO_MAX_SECONDS"] = str(args.audio_max_seconds)
    if args.audio_out_volume is not None:
        if args.audio_out_volume < 0 or args.audio_out_volume > 100:
            raise SystemExit("--audio-out-volume must be between 0 and 100")
        updates["WEARABLLM_AUDIO_OUT_VOLUME"] = str(args.audio_out_volume)
    if args.tts_max_bytes is not None:
        if args.tts_max_bytes < 4096 or args.tts_max_bytes > 1048576:
            raise SystemExit("--tts-max-bytes must be between 4096 and 1048576")
        updates["WEARABLLM_TTS_MAX_BYTES"] = str(args.tts_max_bytes)

    for key, value in updates.items():
        lines = set_kconfig_value(lines, key, value)
    if args.enable_audio_out or args.enable_tts:
        lines = set_kconfig_bool(lines, "WEARABLLM_AUDIO_OUT_ENABLED", True)
    if args.disable_audio_out:
        lines = set_kconfig_bool(lines, "WEARABLLM_AUDIO_OUT_ENABLED", False)
        lines = set_kconfig_bool(lines, "WEARABLLM_TTS_ENABLED", False)
    if args.enable_tts:
        lines = set_kconfig_bool(lines, "WEARABLLM_TTS_ENABLED", True)
    if args.disable_tts:
        lines = set_kconfig_bool(lines, "WEARABLLM_TTS_ENABLED", False)
    if args.enable_direct_openai:
        if not args.openai_api_key and not current.get("openai_api_key_set"):
            raise SystemExit("--enable-direct-openai requires WEARABLLM_OPENAI_API_KEY")
        lines = set_kconfig_bool(lines, "WEARABLLM_DIRECT_OPENAI", True)
        lines = set_kconfig_bool(lines, "WEARABLLM_AUDIO_OUT_ENABLED", True)
    if args.disable_direct_openai:
        lines = set_kconfig_bool(lines, "WEARABLLM_DIRECT_OPENAI", False)
    if args.enable_transcript_log:
        has_url = bool(args.transcript_log_url or current.get("transcript_log_url"))
        has_token = bool(args.transcript_device_token or current.get("transcript_device_token_set"))
        if not has_url or not has_token:
            raise SystemExit(
                "--enable-transcript-log requires WEARABLLM_TRANSCRIPT_LOG_URL and "
                "WEARABLLM_TRANSCRIPT_DEVICE_TOKEN"
            )
        lines = set_kconfig_bool(lines, "WEARABLLM_TRANSCRIPT_LOG_ENABLED", True)
    if args.disable_transcript_log:
        lines = set_kconfig_bool(lines, "WEARABLLM_TRANSCRIPT_LOG_ENABLED", False)
    if args.enable_led_self_test:
        lines = set_kconfig_bool(lines, "WEARABLLM_LED_SELF_TEST_ON_BOOT", True)
    if args.disable_led_self_test:
        lines = set_kconfig_bool(lines, "WEARABLLM_LED_SELF_TEST_ON_BOOT", False)
    if args.enable_display or args.enable_display_self_test:
        lines = set_kconfig_bool(lines, "WEARABLLM_DISPLAY_ENABLED", True)
    if args.disable_display:
        lines = set_kconfig_bool(lines, "WEARABLLM_DISPLAY_ENABLED", False)
    if args.enable_display_self_test:
        lines = set_kconfig_bool(lines, "WEARABLLM_DISPLAY_SELF_TEST_ON_BOOT", True)
    if args.disable_display or args.disable_display_self_test:
        lines = set_kconfig_bool(lines, "WEARABLLM_DISPLAY_SELF_TEST_ON_BOOT", False)
    if args.ptt_pull:
        lines = set_kconfig_choice(
            lines,
            [
                "WEARABLLM_PTT_PULL_NONE",
                "WEARABLLM_PTT_PULL_UP",
                "WEARABLLM_PTT_PULL_DOWN",
            ],
            ptt_pull_choice(args.ptt_pull),
        )

    print("Firmware local config:")
    print(f"  sdkconfig: {sdkconfig}")
    print(f"  Wi-Fi SSID: {masked(args.ssid) if args.ssid else '(unchanged)'}")
    print(f"  Wi-Fi password: {masked(args.password) if args.password else '(unchanged)'}")
    print(
        "  Wi-Fi BSSID: "
        + ("(cleared)" if args.clear_bssid else bssid if bssid else "(unchanged)")
    )
    print(f"  bridge URL: {bridge_url}")
    print(f"  TTS URL: {tts_url}")
    if args.bridge_auth_token:
        print(f"  bridge device token: {masked(args.bridge_auth_token)}")
    if args.device_id:
        print(f"  device ID: {args.device_id}")
    if args.enable_direct_openai:
        print("  direct OpenAI: enabled (API key embedded in firmware)")
    if args.disable_direct_openai:
        print("  direct OpenAI: disabled")
    if args.openai_api_key:
        print(f"  OpenAI API key: {masked(args.openai_api_key)}")
    if args.clear_openai_api_key:
        print("  OpenAI API key: cleared from firmware configuration")
    if args.enable_transcript_log:
        print("  transcript logging: enabled")
    if args.disable_transcript_log:
        print("  transcript logging: disabled")
    if args.transcript_log_url:
        print(f"  transcript log URL: {args.transcript_log_url}")
    if args.transcript_device_id:
        print(f"  transcript device ID: {args.transcript_device_id}")
    if args.transcript_device_token:
        print(f"  transcript device token: {masked(args.transcript_device_token)}")
    if args.ptt_gpio is not None:
        print(f"  PTT GPIO: {args.ptt_gpio}")
    if args.ptt_active_level is not None:
        print(f"  PTT active level: {args.ptt_active_level}")
    if args.ptt_debounce_ms is not None:
        print(f"  PTT debounce ms: {args.ptt_debounce_ms}")
    if args.ptt_pull:
        print(f"  PTT pull: {args.ptt_pull}")
    if args.wifi_timeout_ms is not None:
        print(f"  Wi-Fi timeout ms: {args.wifi_timeout_ms}")
    if args.audio_min_capture_ms is not None:
        print(f"  audio min capture ms: {args.audio_min_capture_ms}")
    if args.audio_max_seconds is not None:
        print(f"  audio max seconds: {args.audio_max_seconds}")
    if args.enable_audio_out:
        print("  speaker output enabled: yes")
    if args.disable_audio_out:
        print("  speaker output enabled: no")
    if args.audio_out_volume is not None:
        print(f"  speaker output volume: {args.audio_out_volume}")
    if args.enable_tts:
        print("  TTS playback enabled: yes")
    if args.disable_tts:
        print("  TTS playback enabled: no")
    if args.tts_max_bytes is not None:
        print(f"  TTS max bytes: {args.tts_max_bytes}")
    if args.enable_led_self_test:
        print("  RGB ring boot self-test: yes")
    if args.disable_led_self_test:
        print("  RGB ring boot self-test: no")
    if args.enable_display:
        print("  TFT display enabled: yes")
    if args.disable_display:
        print("  TFT display enabled: no")
    if args.enable_display_self_test:
        print("  TFT boot self-test: yes")
    if args.disable_display_self_test:
        print("  TFT boot self-test: no")

    if args.dry_run:
        print("Dry run only; no files changed.")
        return 0

    sdkconfig.parent.mkdir(parents=True, exist_ok=True)
    if sdkconfig.exists():
        backup = sdkconfig.with_suffix(sdkconfig.suffix + ".bak")
        shutil.copy2(sdkconfig, backup)
        print(f"  backup: {backup}")
    sdkconfig.write_text("\n".join(lines) + "\n")
    print("Updated sdkconfig. Rebuild and flash firmware for changes to take effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
