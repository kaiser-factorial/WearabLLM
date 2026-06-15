#!/usr/bin/env python3
"""Summarize the latest board serial log and bridge WAV capture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
V3_DIR = SCRIPT_DIR.parent
BRIDGE_DIR = V3_DIR / "bridge"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(BRIDGE_DIR))

from analyze_serial_log import analyze, newest_log  # noqa: E402
from wearabllm_bridge import inspect_wav  # noqa: E402


def newest_wav() -> Path | None:
    capture_dir = BRIDGE_DIR / "captures"
    if not capture_dir.exists():
        return None
    wavs = sorted(capture_dir.glob("*.wav"), key=lambda item: item.stat().st_mtime)
    return wavs[-1] if wavs else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report first-loop bench status from the latest serial log and saved WAV capture."
    )
    parser.add_argument("--serial-log", type=Path, default=None, help="Serial log to analyze. Defaults to newest logs/serial-*.log.")
    parser.add_argument("--wav", type=Path, default=None, help="WAV capture to inspect. Defaults to newest bridge/captures/*.wav.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--require-loop", action="store_true", help="Exit nonzero unless the serial log shows a complete board loop.")
    parser.add_argument("--require-audible", action="store_true", help="Exit nonzero unless the WAV capture is valid and non-silent.")
    return parser.parse_args()


def wav_result(path: Path | None) -> dict[str, object]:
    if path is None:
        return {
            "path": None,
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


def format_human(serial: dict[str, object] | None, wav: dict[str, object]) -> str:
    lines = ["WearabLLM v3 bench report"]
    lines.append("")

    if serial is None:
        lines.append("Serial: no log found")
    else:
        lines.append(f"Serial: {serial['path']}")
        lines.append(f"  loop complete: {'yes' if serial['loop_ok'] else 'no'}")
        if serial.get("wifi_ip"):
            lines.append(f"  Wi-Fi IP: {serial['wifi_ip']}")
        ap = serial.get("wifi_ap")
        if isinstance(ap, dict):
            lines.append(
                "  Wi-Fi AP: "
                f"{ap.get('ssid')} bssid={ap.get('bssid')} "
                f"channel={ap.get('channel')} rssi={ap.get('rssi')}"
            )
        command = serial.get("command")
        if isinstance(command, dict):
            lines.append(f"  command: {command.get('command')} reply_len={command.get('reply_len')}")
        capture = serial.get("capture")
        if isinstance(capture, dict):
            lines.append(
                "  capture: "
                f"{capture.get('duration')} ms, peak={capture.get('peak')}, "
                f"rms={capture.get('rms')}, silent={capture.get('silent')}"
            )

    lines.append("")
    if not wav["exists"]:
        lines.append("WAV: no capture found")
    else:
        info = wav.get("info")
        assert isinstance(info, dict)
        lines.append(f"WAV: {wav['path']}")
        if not wav["valid"]:
            lines.append(f"  invalid: {info.get('error', 'unknown error')}")
        else:
            lines.append(
                "  "
                f"{info.get('sample_rate')} Hz, {info.get('channels')} ch, "
                f"{info.get('duration_ms')} ms"
            )
            lines.append(
                "  "
                f"peak={info.get('peak_abs')} ({info.get('peak_dbfs')} dBFS), "
                f"rms={info.get('rms')} ({info.get('rms_dbfs')} dBFS), "
                f"appears_silent={'yes' if info.get('appears_silent') else 'no'}"
            )

    lines.append("")
    loop_ok = bool(serial and serial.get("loop_ok"))
    audible_ok = bool(wav["valid"] and wav["appears_silent"] is False)
    lines.append(f"Loop gate: {'pass' if loop_ok else 'pending'}")
    lines.append(f"Audio gate: {'pass' if audible_ok else 'pending'}")
    if not loop_ok:
        lines.append("Next: capture a serial log while holding PTT through one bridge response.")
    elif not audible_ok:
        lines.append("Next: inspect mic wiring/codec path before moving to live STT.")
    else:
        lines.append("Next: try live STT/LLM or enable the display path.")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    serial_path = args.serial_log.expanduser() if args.serial_log else newest_log()
    wav_path = args.wav.expanduser() if args.wav else newest_wav()

    serial = analyze(serial_path) if serial_path and serial_path.exists() else None
    wav = wav_result(wav_path if wav_path and wav_path.exists() else None)
    payload = {
        "serial": serial,
        "wav": wav,
        "loop_ok": bool(serial and serial.get("loop_ok")),
        "audible_ok": bool(wav["valid"] and wav["appears_silent"] is False),
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_human(serial, wav))

    if args.require_loop and not payload["loop_ok"]:
        return 2
    if args.require_audible and not payload["audible_ok"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
