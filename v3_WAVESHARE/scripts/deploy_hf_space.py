#!/usr/bin/env python3
"""Publish the hosted WearabLLM bridge scaffold to a private Hugging Face Space.

The script uploads only the files required by the Space; it never reads,
prints, or uploads local firmware sdkconfig files or any other secrets.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
V3_DIR = SCRIPT_DIR.parent
BRIDGE_DIR = V3_DIR / "bridge"
SPACE_DIR = V3_DIR / "hosted_agent"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-id", required=True, help="Hugging Face Space repo, e.g. account/wearabllm-agent")
    parser.add_argument("--public", action="store_true", help="Create a public Space with public source code (not recommended).")
    parser.add_argument("--dry-run", action="store_true", help="Show the selected files without contacting Hugging Face.")
    return parser.parse_args()


def staged_space(destination: Path) -> list[Path]:
    shutil.copy2(SPACE_DIR / "Dockerfile", destination / "Dockerfile")
    shutil.copy2(SPACE_DIR / "README.md", destination / "README.md")
    bridge_destination = destination / "bridge"
    bridge_destination.mkdir()
    files = [
        "action_queue.py",
        "agent_config.py",
        "durable_memory.py",
        "wearabllm_bridge.py",
        "requirements.txt",
    ]
    for name in files:
        shutil.copy2(BRIDGE_DIR / name, bridge_destination / name)
    return [destination / "Dockerfile", destination / "README.md", *(bridge_destination / name for name in files)]


def main() -> int:
    args = parse_args()
    with tempfile.TemporaryDirectory(prefix="wearabllm-hf-space-") as temporary:
        staging = Path(temporary)
        files = staged_space(staging)
        print("Space upload will include:")
        for path in files:
            print(f"  {path.relative_to(staging)}")
        if args.dry_run:
            return 0
        try:
            from huggingface_hub import HfApi
        except ImportError as exc:
            raise SystemExit("Install the uploader first: python -m pip install huggingface_hub") from exc

        api = HfApi()
        api.create_repo(
            repo_id=args.repo_id,
            repo_type="space",
            space_sdk="docker",
            visibility="public" if args.public else "protected",
            exist_ok=True,
        )
        api.upload_folder(
            repo_id=args.repo_id,
            repo_type="space",
            folder_path=str(staging),
            commit_message="Deploy WearabLLM unified-agent bridge",
        )
    print(f"Published https://huggingface.co/spaces/{args.repo_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
