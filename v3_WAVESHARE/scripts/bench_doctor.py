#!/usr/bin/env python3
"""Readiness check for the WearabLLM v3 bench loop."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
V3_DIR = SCRIPT_DIR.parent
BRIDGE_DIR = V3_DIR / "bridge"
CONFIGURE_FIRMWARE = SCRIPT_DIR / "configure_firmware.py"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(BRIDGE_DIR))

from analyze_serial_log import analyze, newest_log  # noqa: E402
from wearabllm_bridge import inspect_wav  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check local firmware config, bridge health, latest serial log, "
            "and latest bridge WAV capture without flashing or resetting the board."
        )
    )
    parser.add_argument(
        "--bridge-url",
        default="",
        help="Bridge base URL or /v1/query URL. Defaults to firmware config bridge URL.",
    )
    parser.add_argument("--serial-log", type=Path, default=None, help="Serial log to analyze.")
    parser.add_argument("--wav", type=Path, default=None, help="WAV capture to inspect.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit nonzero unless firmware config and bridge health are ready for a dry-run bench test.",
    )
    return parser.parse_args()


def normalize_bridge_base_url(raw_url: str) -> str:
    url = raw_url.strip().rstrip("/")
    for suffix in ("/v1/query_text", "/v1/query", "/v1/tts"):
        if url.endswith(suffix):
            return url[: -len(suffix)]
    return url


def firmware_status() -> dict[str, Any]:
    result = subprocess.run(
        [str(CONFIGURE_FIRMWARE), "--status-json"],
        cwd=str(V3_DIR),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return {
            "available": False,
            "ready": False,
            "error": (result.stderr or result.stdout).strip() or f"configure_firmware.py exited {result.returncode}",
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {
            "available": False,
            "ready": False,
            "error": f"invalid configure_firmware.py JSON: {exc}",
        }
    if not isinstance(payload, dict):
        return {
            "available": False,
            "ready": False,
            "error": "configure_firmware.py status was not a JSON object",
        }
    payload["available"] = True
    return payload


def bridge_health(base_url: str, timeout_seconds: float) -> dict[str, Any]:
    if not base_url:
        return {
            "reachable": False,
            "error": "bridge URL is empty",
        }

    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
            status = response.status
    except urllib.error.URLError as exc:
        return {
            "reachable": False,
            "url": base_url,
            "error": str(exc),
        }

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return {
            "reachable": False,
            "url": base_url,
            "status": status,
            "error": f"non-JSON health response: {exc}",
        }
    if not isinstance(payload, dict):
        return {
            "reachable": False,
            "url": base_url,
            "status": status,
            "error": "health response was not a JSON object",
        }
    return {
        "reachable": status == 200 and payload.get("ok") is True,
        "url": base_url,
        "status": status,
        "payload": payload,
    }


def newest_wav() -> Path | None:
    capture_dir = BRIDGE_DIR / "captures"
    if not capture_dir.exists():
        return None
    wavs = sorted(capture_dir.glob("*.wav"), key=lambda item: item.stat().st_mtime)
    return wavs[-1] if wavs else None


def wav_status(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {
            "path": None if path is None else str(path),
            "exists": False,
            "valid": False,
            "appears_silent": None,
            "info": None,
        }
    info = inspect_wav(path.read_bytes())
    return {
        "path": str(path),
        "exists": True,
        "valid": bool(info.get("valid")),
        "appears_silent": info.get("appears_silent"),
        "info": info,
    }


def serial_status(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {
            "path": None if path is None else str(path),
            "exists": False,
            "loop_ok": False,
        }
    result = analyze(path)
    result["exists"] = True
    return result


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    firmware = firmware_status()
    configured_bridge = str(firmware.get("bridge_url") or "")
    bridge_base_url = normalize_bridge_base_url(args.bridge_url or configured_bridge)
    bridge = bridge_health(bridge_base_url, timeout_seconds=2.0)
    serial_path = args.serial_log.expanduser() if args.serial_log else newest_log()
    wav_path = args.wav.expanduser() if args.wav else newest_wav()
    serial = serial_status(serial_path)
    wav = wav_status(wav_path)

    bridge_payload = bridge.get("payload")
    bridge_config = bridge_payload.get("config") if isinstance(bridge_payload, dict) else {}
    bridge_config = bridge_config if isinstance(bridge_config, dict) else {}
    ready = bool(
        firmware.get("ready")
        and bridge.get("reachable")
        and bridge_config.get("dry_run") is True
    )

    return {
        "firmware": firmware,
        "bridge": bridge,
        "serial": serial,
        "wav": wav,
        "ready_for_dry_run": ready,
        "loop_ok": bool(serial.get("loop_ok")),
        "audible_ok": bool(wav.get("valid") and wav.get("appears_silent") is False),
    }


def yes_no(value: object) -> str:
    return "yes" if bool(value) else "no"


def format_human(payload: dict[str, Any]) -> str:
    firmware = payload["firmware"]
    bridge = payload["bridge"]
    serial = payload["serial"]
    wav = payload["wav"]

    bridge_payload = bridge.get("payload")
    bridge_config = bridge_payload.get("config") if isinstance(bridge_payload, dict) else {}
    bridge_config = bridge_config if isinstance(bridge_config, dict) else {}

    lines = ["WearabLLM v3 bench doctor", ""]
    lines.append("Firmware config:")
    lines.append(f"  ready: {yes_no(firmware.get('ready'))}")
    lines.append(f"  Wi-Fi SSID: {'set' if firmware.get('wifi_ssid_set') else 'empty'}")
    lines.append(f"  Wi-Fi password: {'set' if firmware.get('wifi_password_set') else 'empty'}")
    lines.append(f"  Wi-Fi BSSID: {firmware.get('wifi_bssid') or 'not pinned'}")
    lines.append(f"  bridge URL: {firmware.get('bridge_url') or 'empty'}")
    lines.append(
        "  PTT: "
        f"GPIO {firmware.get('ptt_gpio') if firmware.get('ptt_gpio') is not None else 'unset'}, "
        f"active {firmware.get('ptt_active_level')}, "
        f"debounce {firmware.get('ptt_debounce_ms')} ms, "
        f"pull {firmware.get('ptt_pull')}"
    )
    lines.append(f"  RGB ring boot test: {'on' if firmware.get('led_self_test') else 'off'}")
    lines.append(
        "  TFT: "
        f"{'on' if firmware.get('display_enabled') else 'off'}, "
        f"boot test {'on' if firmware.get('display_self_test') else 'off'}"
    )
    for item in firmware.get("next") or []:
        lines.append(f"  next: {item}")

    lines.append("")
    lines.append("Bridge:")
    lines.append(f"  reachable: {yes_no(bridge.get('reachable'))}")
    lines.append(f"  URL: {bridge.get('url') or 'empty'}")
    if bridge.get("error"):
        lines.append(f"  error: {bridge['error']}")
    if bridge_config:
        lines.append(f"  dry-run: {yes_no(bridge_config.get('dry_run'))}")
        lines.append(f"  device config: {yes_no(bridge_config.get('device_config'))}")
        lines.append(f"  audio cap: {bridge_config.get('max_audio_bytes') or 'unknown'} bytes")

    lines.append("")
    lines.append("Latest evidence:")
    if serial.get("exists"):
        lines.append(f"  serial: {serial.get('path')}")
        lines.append(f"  serial loop complete: {yes_no(serial.get('loop_ok'))}")
    else:
        lines.append("  serial: none found")
    if wav.get("exists"):
        info = wav.get("info") if isinstance(wav.get("info"), dict) else {}
        lines.append(f"  WAV: {wav.get('path')}")
        lines.append(
            "  WAV audio: "
            f"{'valid' if wav.get('valid') else 'invalid'}, "
            f"silent={yes_no(wav.get('appears_silent'))}, "
            f"{info.get('duration_ms', 'unknown')} ms"
        )
    else:
        lines.append("  WAV: none found")

    lines.append("")
    lines.append(f"Ready for dry-run bench: {yes_no(payload['ready_for_dry_run'])}")
    if not payload["ready_for_dry_run"]:
        if not firmware.get("ready"):
            lines.append("Next: finish firmware Wi-Fi/bridge config, then rebuild and flash.")
        elif not bridge.get("reachable"):
            lines.append("Next: start the dry-run bridge or fix the bridge URL.")
        elif bridge_config.get("dry_run") is not True:
            lines.append("Next: use dry-run bridge mode for the first board loop.")
    elif not payload["loop_ok"]:
        lines.append("Next: flash/monitor, hold PTT, speak, release, then save a serial log.")
    elif not payload["audible_ok"]:
        lines.append("Next: inspect the saved WAV and mic path before live STT.")
    else:
        lines.append("Next: try live STT/LLM or enable the normal TFT response display.")

    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    payload = build_payload(args)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_human(payload))
    if args.require_ready and not payload["ready_for_dry_run"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
