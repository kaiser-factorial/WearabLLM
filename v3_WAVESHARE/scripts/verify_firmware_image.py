#!/usr/bin/env python3
"""Verify that the staged WearabLLM firmware image matches local configuration."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
V3_DIR = SCRIPT_DIR.parent
FIRMWARE_DIR = V3_DIR / "firmware"
sys.path.insert(0, str(SCRIPT_DIR))

from configure_firmware import parse_kconfig_string, status_payload  # noqa: E402

DEFINE_RE = re.compile(r"^#define CONFIG_(?P<key>[A-Z0-9_]+)(?:\s+(?P<value>.*))?$")
FEATURES = {
    "audio_out_enabled": "WEARABLLM_AUDIO_OUT_ENABLED",
    "tts_enabled": "WEARABLLM_TTS_ENABLED",
    "led_self_test": "WEARABLLM_LED_SELF_TEST_ON_BOOT",
    "display_enabled": "WEARABLLM_DISPLAY_ENABLED",
    "display_self_test": "WEARABLLM_DISPLAY_SELF_TEST_ON_BOOT",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check that the built ESP32-S3 image is current and matches firmware/sdkconfig."
    )
    parser.add_argument("--firmware-dir", type=Path, default=FIRMWARE_DIR)
    parser.add_argument(
        "--require-first-flash-profile",
        action="store_true",
        help="Require TFT, speaker, TTS, and LED boot self-test to be disabled.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def parse_generated_config(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(errors="replace").splitlines():
        match = DEFINE_RE.match(line.strip())
        if match:
            values[match.group("key")] = (match.group("value") or "1").strip()
    return values


def generated_string(values: dict[str, str], key: str) -> str | None:
    raw = values.get(key)
    if raw is None:
        return None
    return parse_kconfig_string(raw)


def generated_bool(values: dict[str, str], key: str) -> bool:
    return values.get(key) not in (None, "0")


def firmware_inputs(firmware_dir: Path) -> list[Path]:
    inputs = [
        firmware_dir / "CMakeLists.txt",
        firmware_dir / "dependencies.lock",
        firmware_dir / "partitions.csv",
        firmware_dir / "sdkconfig",
        firmware_dir / "sdkconfig.defaults",
    ]
    main_dir = firmware_dir / "main"
    if main_dir.exists():
        inputs.extend(path for path in main_dir.rglob("*") if path.is_file())
    return [path for path in inputs if path.exists()]


def add_check(checks: list[dict[str, Any]], name: str, ok: bool, detail: str) -> None:
    checks.append({"name": name, "ok": ok, "detail": detail})


def verify(firmware_dir: Path, require_first_flash_profile: bool) -> dict[str, Any]:
    firmware_dir = firmware_dir.resolve()
    sdkconfig = firmware_dir / "sdkconfig"
    build_dir = firmware_dir / "build"
    config_header = build_dir / "config" / "sdkconfig.h"
    app_binary = build_dir / "wearabllm_waveshare.bin"
    required_artifacts = [
        build_dir / "bootloader" / "bootloader.bin",
        build_dir / "partition_table" / "partition-table.bin",
        app_binary,
    ]

    sdk_lines = sdkconfig.read_text(errors="replace").splitlines() if sdkconfig.exists() else []
    staged = status_payload(sdkconfig, sdk_lines)
    generated = parse_generated_config(config_header)
    checks: list[dict[str, Any]] = []

    add_check(checks, "firmware_config_ready", bool(staged["ready"]), "Wi-Fi fields and bridge URL are staged")
    missing = [str(path) for path in required_artifacts if not path.exists()]
    add_check(
        checks,
        "flash_artifacts_present",
        not missing,
        "bootloader, partition table, and app image exist" if not missing else "missing: " + ", ".join(missing),
    )
    add_check(
        checks,
        "generated_config_present",
        config_header.exists(),
        str(config_header),
    )

    staged_bridge = str(staged.get("bridge_url") or "")
    built_bridge = generated_string(generated, "WEARABLLM_BRIDGE_URL")
    add_check(
        checks,
        "bridge_config_matches_build",
        bool(staged_bridge) and staged_bridge == built_bridge,
        f"staged={staged_bridge or '(empty)'} built={built_bridge or '(empty)'}",
    )

    binary_data = app_binary.read_bytes() if app_binary.exists() else b""
    add_check(
        checks,
        "bridge_url_embedded",
        bool(staged_bridge) and staged_bridge.encode() in binary_data,
        staged_bridge or "bridge URL is empty",
    )

    newest_input = max(firmware_inputs(firmware_dir), key=lambda path: path.stat().st_mtime, default=None)
    image_fresh = bool(
        app_binary.exists()
        and newest_input is not None
        and app_binary.stat().st_mtime >= newest_input.stat().st_mtime
    )
    add_check(
        checks,
        "image_is_current",
        image_fresh,
        f"newest input: {newest_input}" if newest_input else "no firmware inputs found",
    )

    for status_key, config_key in FEATURES.items():
        staged_enabled = bool(staged.get(status_key))
        built_enabled = generated_bool(generated, config_key)
        add_check(
            checks,
            f"{status_key}_matches_build",
            staged_enabled == built_enabled,
            f"staged={'on' if staged_enabled else 'off'} built={'on' if built_enabled else 'off'}",
        )

    if require_first_flash_profile:
        enabled = [status_key for status_key in FEATURES if bool(staged.get(status_key))]
        add_check(
            checks,
            "first_flash_profile",
            not enabled,
            "optional display/audio/TTS/self-test paths are off"
            if not enabled
            else "enabled: " + ", ".join(enabled),
        )

    digest = hashlib.sha256(binary_data).hexdigest() if binary_data else None
    return {
        "ok": all(bool(check["ok"]) for check in checks),
        "firmware_dir": str(firmware_dir),
        "binary": str(app_binary),
        "binary_bytes": len(binary_data),
        "binary_sha256": digest,
        "bridge_url": staged.get("bridge_url"),
        "profile": {key: bool(staged.get(key)) for key in FEATURES},
        "checks": checks,
    }


def format_human(result: dict[str, Any]) -> str:
    lines = ["WearabLLM v3 firmware image verification", ""]
    for check in result["checks"]:
        lines.append(f"  [{'ok' if check['ok'] else 'FAIL'}] {check['name']}: {check['detail']}")
    lines.extend(
        [
            "",
            f"Binary: {result['binary']}",
            f"Size: {result['binary_bytes']} bytes",
            f"SHA-256: {result['binary_sha256'] or '(unavailable)'}",
            f"Bridge URL: {result['bridge_url'] or '(empty)'}",
            f"Ready to flash: {'yes' if result['ok'] else 'no'}",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    result = verify(args.firmware_dir.expanduser(), args.require_first_flash_profile)
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else format_human(result))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
