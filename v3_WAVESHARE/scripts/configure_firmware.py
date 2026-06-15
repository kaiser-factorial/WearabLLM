#!/usr/bin/env python3
"""Update local ESP-IDF sdkconfig values for WearabLLM v3 bench tests."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
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
        raise SystemExit("Wi-Fi BSSID must look like ca:50:35:23:2b:1f")
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
        help="Optional AP MAC/BSSID, for example ca:50:35:23:2b:1f.",
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
        "--tts-url",
        default=os.environ.get("WEARABLLM_TTS_URL", ""),
        help="Full /v1/tts URL. Defaults to the same host/port as the bridge URL.",
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
    ptt_gpio = get_kconfig_value(lines, "WEARABLLM_PTT_GPIO") or "(unset)"
    ptt_active_level = get_kconfig_value(lines, "WEARABLLM_PTT_ACTIVE_LEVEL") or "0"
    ptt_debounce_ms = get_kconfig_value(lines, "WEARABLLM_PTT_DEBOUNCE_MS") or "35"
    ptt_pull = ptt_pull_status(lines)
    timeout_ms = get_kconfig_value(lines, "WEARABLLM_WIFI_CONNECT_TIMEOUT_MS") or "(unset)"
    audio_min_ms = get_kconfig_value(lines, "WEARABLLM_AUDIO_MIN_CAPTURE_MS") or "250"
    audio_max_s = get_kconfig_value(lines, "WEARABLLM_AUDIO_MAX_SECONDS") or "6"
    led_self_test = kconfig_bool_status(lines, "WEARABLLM_LED_SELF_TEST_ON_BOOT")
    display_enabled = kconfig_bool_status(lines, "WEARABLLM_DISPLAY_ENABLED")
    display_self_test = kconfig_bool_status(lines, "WEARABLLM_DISPLAY_SELF_TEST_ON_BOOT")

    wifi_ready = bool(ssid and password)
    bridge_ready = bridge_url.startswith("http://") or bridge_url.startswith("https://")
    ready = wifi_ready and bridge_ready

    return {
        "sdkconfig": str(sdkconfig),
        "wifi_ssid_set": bool(ssid),
        "wifi_password_set": bool(password),
        "wifi_bssid": bssid or None,
        "bridge_url": bridge_url or None,
        "ptt_gpio": None if ptt_gpio == "(unset)" else int(ptt_gpio),
        "ptt_active_level": int(ptt_active_level),
        "ptt_debounce_ms": int(ptt_debounce_ms),
        "ptt_pull": ptt_pull,
        "wifi_timeout_ms": None if timeout_ms == "(unset)" else int(timeout_ms),
        "audio_min_capture_ms": int(audio_min_ms),
        "audio_max_seconds": int(audio_max_s),
        "led_self_test": led_self_test,
        "display_enabled": display_enabled,
        "display_self_test": display_self_test,
        "ready": ready,
        "next": [
            *([] if wifi_ready else ["set both WEARABLLM_WIFI_SSID and WEARABLLM_WIFI_PASSWORD"]),
            *([] if bridge_ready else ["set bridge URL with --bridge-host or --bridge-url"]),
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
    print(f"  PTT GPIO: {status['ptt_gpio'] if status['ptt_gpio'] is not None else '(unset)'}")
    print(f"  PTT active level: {status['ptt_active_level']}")
    print(f"  PTT debounce ms: {status['ptt_debounce_ms']}")
    print(f"  PTT pull: {status['ptt_pull']}")
    print(f"  Wi-Fi timeout ms: {status['wifi_timeout_ms'] if status['wifi_timeout_ms'] is not None else '(unset)'}")
    print(f"  audio min capture ms: {status['audio_min_capture_ms']}")
    print(f"  audio max seconds: {status['audio_max_seconds']}")
    print(f"  RGB ring boot self-test: {'yes' if status['led_self_test'] else 'no'}")
    print(f"  TFT display enabled: {'yes' if status['display_enabled'] else 'no'}")
    print(f"  TFT boot self-test: {'yes' if status['display_self_test'] else 'no'}")
    print(f"  ready for board-to-bridge dry-run test: {'yes' if status['ready'] else 'no'}")
    for next_step in status["next"]:
        print(f"  next: {next_step}")
    return 0 if status["ready"] else 1


def main() -> int:
    args = parse_args()
    bssid = normalize_bssid(args.bssid)
    bridge_host = args.bridge_host or default_bridge_host()
    bridge_url = args.bridge_url or f"http://{bridge_host}:{args.bridge_port}/v1/query"
    tts_url = args.tts_url or f"http://{bridge_host}:{args.bridge_port}/v1/tts"

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

    if args.status_json:
        print(json.dumps(status_payload(sdkconfig, lines), sort_keys=True))
        return 0

    if args.status:
        return print_status(sdkconfig, lines)

    updates = {
        "WEARABLLM_BRIDGE_URL": kconfig_quote(bridge_url),
        "WEARABLLM_TTS_URL": kconfig_quote(tts_url),
    }
    if args.ssid:
        updates["WEARABLLM_WIFI_SSID"] = kconfig_quote(args.ssid)
    if args.password:
        updates["WEARABLLM_WIFI_PASSWORD"] = kconfig_quote(args.password)
    if bssid:
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

    for key, value in updates.items():
        lines = set_kconfig_value(lines, key, value)
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
    print(f"  Wi-Fi SSID: {masked(args.ssid)}")
    print(f"  Wi-Fi password: {masked(args.password)}")
    print(f"  Wi-Fi BSSID: {bssid or '(not pinned)'}")
    print(f"  bridge URL: {bridge_url}")
    print(f"  TTS URL: {tts_url}")
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
