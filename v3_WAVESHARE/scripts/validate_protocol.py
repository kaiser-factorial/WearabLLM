#!/usr/bin/env python3
"""Check that the WearabLLM 9-command response scale stays consistent."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable

V3_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = V3_DIR.parent
BRIDGE_PATH = V3_DIR / "bridge" / "wearabllm_bridge.py"
APP_COMMANDS_PATH = V3_DIR / "app" / "src" / "protocol" / "commands.ts"
FIRMWARE_MAIN_PATH = V3_DIR / "firmware" / "main" / "main.c"
PROTOCOL_README_PATH = V3_DIR / "protocol" / "README.md"
ROOT_SPEC_PATH = REPO_ROOT / "SPEC.md"

EXPECTED_COMMANDS = ["GS", "GP", "GC", "RS", "RF", "YP", "BS", "PS", "PP"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate v3 LED command protocol consistency.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_bridge_commands() -> list[str]:
    text = read(BRIDGE_PATH)
    match = re.search(r"LED_COMMANDS\s*=\s*\{(.*?)\n\}", text, flags=re.DOTALL)
    if not match:
        raise RuntimeError(f"Cannot find LED_COMMANDS in {BRIDGE_PATH}")
    return re.findall(r'^\s*"([A-Z]{2})"\s*:', match.group(1), flags=re.MULTILINE)


def parse_app_commands() -> list[str]:
    text = read(APP_COMMANDS_PATH)
    match = re.search(r"COMMANDS:\s*LEDCommand\[\]\s*=\s*\[(.*?)\]", text, flags=re.DOTALL)
    if not match:
        raise RuntimeError(f"Cannot find COMMANDS array in {APP_COMMANDS_PATH}")
    return re.findall(r"'([A-Z]{2})'", match.group(1))


def parse_firmware_commands() -> list[str]:
    text = read(FIRMWARE_MAIN_PATH)
    match = re.search(r"static const char \*const commands\[\]\s*=\s*\{(.*?)\};", text, flags=re.DOTALL)
    if not match:
        raise RuntimeError(f"Cannot find firmware self-test command array in {FIRMWARE_MAIN_PATH}")
    commands = re.findall(r'"([A-Z]{2})"', match.group(1))
    seen: list[str] = []
    for command in commands:
        if command not in seen:
            seen.append(command)
    return seen


def parse_markdown_table_commands(path: Path) -> list[str]:
    commands: list[str] = []
    for command in re.findall(r"\|\s*`([A-Z]{2})`\s*\|", read(path)):
        if command not in commands:
            commands.append(command)
    return commands


def compare(name: str, commands: Iterable[str]) -> dict[str, object]:
    actual = list(commands)
    return {
        "name": name,
        "commands": actual,
        "ok": actual == EXPECTED_COMMANDS,
        "missing": [command for command in EXPECTED_COMMANDS if command not in actual],
        "extra": [command for command in actual if command not in EXPECTED_COMMANDS],
    }


def main() -> int:
    args = parse_args()
    results = [
        compare("bridge LED_COMMANDS", load_bridge_commands()),
        compare("Android app COMMANDS", parse_app_commands()),
        compare("firmware command checks", parse_firmware_commands()),
        compare("protocol README table", parse_markdown_table_commands(PROTOCOL_README_PATH)),
        compare("root SPEC table", parse_markdown_table_commands(ROOT_SPEC_PATH)),
    ]
    ok = all(bool(result["ok"]) for result in results)

    payload = {
        "ok": ok,
        "expected": EXPECTED_COMMANDS,
        "results": results,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("WearabLLM command protocol consistency")
        print(f"Expected: {', '.join(EXPECTED_COMMANDS)}")
        for result in results:
            marker = "ok" if result["ok"] else "FAIL"
            print(f"[{marker}] {result['name']}: {', '.join(result['commands'])}")
            if not result["ok"]:
                if result["missing"]:
                    print(f"  missing: {', '.join(result['missing'])}")
                if result["extra"]:
                    print(f"  extra: {', '.join(result['extra'])}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
