#!/usr/bin/env python3
"""Inspect WearabLLM bridge WAV captures for first-hardware bring-up."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

V3_DIR = Path(__file__).resolve().parents[1]
BRIDGE_DIR = V3_DIR / "bridge"
sys.path.insert(0, str(BRIDGE_DIR))

from wearabllm_bridge import inspect_wav  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect one or more WAV captures saved by the WearabLLM bridge."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="WAV file(s) or directories. Defaults to bridge/captures.",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Inspect only the newest matching WAV.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of a human summary.",
    )
    parser.add_argument(
        "--require-audible",
        action="store_true",
        help="Exit nonzero unless every inspected WAV is valid and not marked silent.",
    )
    return parser.parse_args()


def expand_paths(paths: list[Path]) -> list[Path]:
    if not paths:
        paths = [BRIDGE_DIR / "captures"]

    wavs: list[Path] = []
    for path in paths:
        expanded = path.expanduser()
        if expanded.is_dir():
            wavs.extend(sorted(expanded.glob("*.wav")))
        elif expanded.exists():
            wavs.append(expanded)
        else:
            print(f"missing: {expanded}", file=sys.stderr)
    return sorted(wavs, key=lambda item: item.stat().st_mtime)


def format_human(path: Path, info: dict[str, object]) -> str:
    if not info.get("valid"):
        return f"{path}\n  invalid WAV: {info.get('error', 'unknown error')}"

    silence = "yes" if info.get("appears_silent") else "no"
    return "\n".join(
        [
            str(path),
            (
                "  "
                f"{info.get('sample_rate')} Hz, "
                f"{info.get('channels')} ch, "
                f"{info.get('sample_width_bytes')} bytes/sample, "
                f"{info.get('duration_ms')} ms"
            ),
            (
                "  "
                f"peak={info.get('peak_abs')} "
                f"({info.get('peak_dbfs')} dBFS), "
                f"rms={info.get('rms')} "
                f"({info.get('rms_dbfs')} dBFS), "
                f"appears_silent={silence}"
            ),
        ]
    )


def main() -> int:
    args = parse_args()
    wavs = expand_paths(args.paths)
    if args.latest and wavs:
        wavs = [wavs[-1]]

    if not wavs:
        print("No WAV captures found.", file=sys.stderr)
        return 1

    results = [{"path": str(path), "info": inspect_wav(path.read_bytes())} for path in wavs]
    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        print("\n\n".join(format_human(Path(item["path"]), item["info"]) for item in results))
    if args.require_audible:
        for item in results:
            info = item["info"]
            if not isinstance(info, dict) or not info.get("valid") or info.get("appears_silent"):
                return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
