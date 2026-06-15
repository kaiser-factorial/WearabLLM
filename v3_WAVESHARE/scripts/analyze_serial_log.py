#!/usr/bin/env python3
"""Summarize WearabLLM firmware serial logs for first hardware bring-up."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

V3_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = V3_DIR / "logs"


CHECKS = [
    ("boot", "WearabLLM v3 app reached", "WearabLLM v3 Waveshare phase-1 scaffold"),
    ("display", "display initialized or disabled", "wearabllm_display:"),
    ("audio_ready", "ES7210 mic ready", "ES7210 microphone capture ready"),
    ("wifi_configured", "Wi-Fi credentials configured", "Wi-Fi SSID configured=yes"),
    ("wifi_connected", "Wi-Fi connected", "Wi-Fi connected:"),
    ("wifi_ap", "Wi-Fi AP details logged", "Wi-Fi AP:"),
    ("listening", "PTT/listening state seen", "push-to-talk held: listening"),
    ("capture_stats", "capture stats seen", "capture stats:"),
    ("bridge_post", "posted WAV to bridge", "posting WAV to bridge:"),
    ("bridge_ok", "bridge HTTP 200 response", "bridge HTTP result: err=ESP_OK status=200"),
    ("command", "bridge command parsed", "bridge command="),
    ("led", "LED command applied", "LED command:"),
]

ERROR_PATTERNS = [
    re.compile(r"\bE \([^)]+\) ([^:]+): (.+)"),
    re.compile(r"\bERROR\b[: ](.+)"),
]

CAPTURE_RE = re.compile(
    r"capture stats: duration=(?P<duration>\d+) ms "
    r"samples=(?P<samples>\d+) peak=(?P<peak>\d+) rms=(?P<rms>\d+) "
    r"appears_silent=(?P<silent>yes|no)"
)
BRIDGE_RE = re.compile(r"bridge HTTP result: err=(?P<err>\S+) status=(?P<status>\d+)")
COMMAND_RE = re.compile(r"bridge command=(?P<command>[A-Z]{2}) reply_len=(?P<reply_len>\d+)")
IP_RE = re.compile(r"Wi-Fi connected: (?P<ip>\d+\.\d+\.\d+\.\d+)")
AP_RE = re.compile(
    r"Wi-Fi AP: ssid=(?P<ssid>.*?) "
    r"bssid=(?P<bssid>[0-9a-fA-F:]{17}) "
    r"channel=(?P<channel>\d+) rssi=(?P<rssi>-?\d+) auth=(?P<auth>\d+)"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze a saved WearabLLM ESP32 serial log and summarize bring-up status."
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        help="Serial log path. Defaults to the newest logs/serial-*.log file.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a human report.")
    parser.add_argument(
        "--require-loop",
        action="store_true",
        help="Exit nonzero unless Wi-Fi, capture, bridge HTTP 200, and command parsing are all observed.",
    )
    return parser.parse_args()


def newest_log() -> Path | None:
    if not DEFAULT_LOG_DIR.exists():
        return None
    logs = sorted(DEFAULT_LOG_DIR.glob("serial-*.log"), key=lambda item: item.stat().st_mtime)
    return logs[-1] if logs else None


def read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


def first_match(pattern: re.Pattern[str], text: str) -> dict[str, str] | None:
    match = pattern.search(text)
    return match.groupdict() if match else None


def collect_errors(lines: list[str]) -> list[str]:
    errors: list[str] = []
    for line in lines:
        if "firmware_flash_monitor.sh" in line:
            continue
        for pattern in ERROR_PATTERNS:
            match = pattern.search(line)
            if match:
                errors.append(line.strip())
                break
    return errors[-8:]


def analyze(path: Path) -> dict[str, object]:
    text = read_text(path)
    lines = text.splitlines()
    checks = {
        key: {
            "ok": needle in text,
            "label": label,
        }
        for key, label, needle in CHECKS
    }

    capture = first_match(CAPTURE_RE, text)
    bridge = first_match(BRIDGE_RE, text)
    command = first_match(COMMAND_RE, text)
    ip = first_match(IP_RE, text)
    ap = first_match(AP_RE, text)

    loop_ok = all(
        checks[key]["ok"]
        for key in ("wifi_connected", "capture_stats", "bridge_post", "bridge_ok", "command")
    )

    return {
        "path": str(path),
        "line_count": len(lines),
        "checks": checks,
        "loop_ok": loop_ok,
        "wifi_ip": ip["ip"] if ip else None,
        "wifi_ap": ap,
        "capture": capture,
        "bridge": bridge,
        "command": command,
        "recent_errors": collect_errors(lines),
    }


def format_human(result: dict[str, object]) -> str:
    checks = result["checks"]
    assert isinstance(checks, dict)

    lines = [
        f"Serial log: {result['path']}",
        f"Lines: {result['line_count']}",
        f"Board loop complete: {'yes' if result['loop_ok'] else 'no'}",
    ]

    if result.get("wifi_ip"):
        lines.append(f"Wi-Fi IP: {result['wifi_ip']}")

    ap = result.get("wifi_ap")
    if isinstance(ap, dict):
        lines.append(
            "Wi-Fi AP: "
            f"{ap.get('ssid')} bssid={ap.get('bssid')} "
            f"channel={ap.get('channel')} rssi={ap.get('rssi')}"
        )

    command = result.get("command")
    if isinstance(command, dict):
        lines.append(f"Command: {command.get('command')} reply_len={command.get('reply_len')}")

    capture = result.get("capture")
    if isinstance(capture, dict):
        lines.append(
            "Capture: "
            f"{capture.get('duration')} ms, peak={capture.get('peak')}, "
            f"rms={capture.get('rms')}, silent={capture.get('silent')}"
        )

    bridge = result.get("bridge")
    if isinstance(bridge, dict):
        lines.append(f"Bridge: err={bridge.get('err')} status={bridge.get('status')}")

    lines.append("")
    lines.append("Checks:")
    for key, data in checks.items():
        assert isinstance(data, dict)
        marker = "ok" if data["ok"] else "--"
        lines.append(f"  [{marker}] {data['label']} ({key})")

    errors = result.get("recent_errors")
    if isinstance(errors, list) and errors:
        lines.append("")
        lines.append("Recent errors:")
        lines.extend(f"  {line}" for line in errors)

    if not result["loop_ok"]:
        lines.append("")
        lines.append("Next likely checks:")
        if not checks["wifi_connected"]["ok"]:
            lines.append("  - confirm bridge computer and ESP32 are on the same Wi-Fi network")
            lines.append("  - confirm firmware was rebuilt/flashed after setting credentials")
        elif not checks["capture_stats"]["ok"]:
            lines.append("  - hold BOOT/PTT, speak, then release while monitor is running")
        elif not checks["bridge_ok"]["ok"]:
            lines.append("  - confirm run_bridge_dryrun.sh is still running and bridge URL matches this computer")
        elif not checks["command"]["ok"]:
            lines.append("  - inspect the bridge response body and firmware JSON parsing logs")

    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    path = args.path.expanduser() if args.path else newest_log()
    if path is None:
        print(f"No serial logs found under {DEFAULT_LOG_DIR}", file=sys.stderr)
        return 1
    if not path.exists():
        print(f"Serial log not found: {path}", file=sys.stderr)
        return 1

    result = analyze(path)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_human(result))

    if args.require_loop and not result["loop_ok"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
