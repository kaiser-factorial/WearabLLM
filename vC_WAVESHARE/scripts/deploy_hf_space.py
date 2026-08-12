#!/usr/bin/env python3
"""Publish the hosted WearabLLM bridge scaffold to a private Hugging Face Space.

The script uploads only the files required by the Space; it never reads,
prints, or uploads local firmware sdkconfig files or any other secrets.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
V3_DIR = SCRIPT_DIR.parent
BRIDGE_DIR = V3_DIR / "bridge"
SPACE_DIR = V3_DIR / "hosted_agent"
REPO_DIR = V3_DIR.parent
SOURCE_PATTERNS = (
    "README.md",
    "supabase/README.md",
    "supabase/migrations/*.sql",
    "vC_WAVESHARE/README.md",
    "vC_WAVESHARE/docs/*.md",
    "vC_WAVESHARE/bridge/*.py",
    "vC_WAVESHARE/protocol/*.md",
    "vC_WAVESHARE/hosted_agent/Dockerfile",
    "vC_WAVESHARE/hosted_agent/README.md",
    "vC_WAVESHARE/scripts/*.py",
    "vC_WAVESHARE/scripts/*.sh",
    "vC_WAVESHARE/transcript_viewer/README.md",
    "vC_WAVESHARE/transcript_viewer/server.py",
    "vC_WAVESHARE/transcript_viewer/static/*.html",
    "vC_WAVESHARE/transcript_viewer/static/*.css",
    "vC_WAVESHARE/transcript_viewer/static/*.js",
    "vC_WAVESHARE/app/README.md",
    "vC_WAVESHARE/app/package.json",
    "vC_WAVESHARE/app/src/**/*.ts",
    "vC_WAVESHARE/app/src/**/*.tsx",
    "vC_WAVESHARE/firmware/main/*.c",
    "vC_WAVESHARE/firmware/main/*.h",
    "vC_WAVESHARE/firmware/main/*.yml",
    "vC_WAVESHARE/firmware/main/CMakeLists.txt",
    "vC_WAVESHARE/firmware/main/Kconfig.projbuild",
)
FORBIDDEN_SOURCE_PARTS = {".git", "build", "node_modules", "captures", "private", "secrets"}
FORBIDDEN_SOURCE_NAMES = (".env", "sdkconfig", "credential", "secret", "id_rsa")
MAX_SOURCE_FILE_BYTES = 256 * 1024
MAX_SOURCE_BUNDLE_BYTES = 4 * 1024 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-id", required=True, help="Hugging Face Space repo, e.g. account/wearabllm-agent")
    parser.add_argument("--public", action="store_true", help="Create a public Space with public source code (not recommended).")
    parser.add_argument("--dry-run", action="store_true", help="Show the selected files without contacting Hugging Face.")
    return parser.parse_args()


def build_source_bundle(destination: Path) -> Path:
    """Create the private Space's opt-in, read-only self-source manifest."""
    selected: set[Path] = set()
    for pattern in SOURCE_PATTERNS:
        selected.update(path for path in REPO_DIR.glob(pattern) if path.is_file())
    files: dict[str, str] = {}
    total_bytes = 0
    for path in sorted(selected):
        relative = path.relative_to(REPO_DIR).as_posix()
        lowered_parts = {part.lower() for part in path.relative_to(REPO_DIR).parts}
        lowered_name = path.name.lower()
        if path.is_symlink() or lowered_parts & FORBIDDEN_SOURCE_PARTS:
            raise RuntimeError(f"Refusing unsafe source-manifest path: {relative}")
        if any(marker in lowered_name for marker in FORBIDDEN_SOURCE_NAMES):
            raise RuntimeError(f"Refusing sensitive source-manifest path: {relative}")
        size = path.stat().st_size
        if size > MAX_SOURCE_FILE_BYTES:
            raise RuntimeError(f"Source-manifest file is too large: {relative} ({size} bytes)")
        content = path.read_text(encoding="utf-8")
        total_bytes += len(content.encode("utf-8"))
        if total_bytes > MAX_SOURCE_BUNDLE_BYTES:
            raise RuntimeError("Sphere source manifest exceeds the 4 MiB deployment limit")
        files[relative] = content
    if not files:
        raise RuntimeError("Sphere source manifest is empty")
    output = destination / "source_bundle.json"
    output.write_text(json.dumps({"version": 1, "files": files}), encoding="utf-8")
    return output


def staged_space(destination: Path) -> list[Path]:
    shutil.copy2(SPACE_DIR / "Dockerfile", destination / "Dockerfile")
    shutil.copy2(SPACE_DIR / "README.md", destination / "README.md")
    bridge_destination = destination / "bridge"
    bridge_destination.mkdir()
    files = [
        "action_queue.py",
        "agent_config.py",
        "bridge_contracts.py",
        "bridge_policy.py",
        "bridge_service.py",
        "device_config.py",
        "durable_memory.py",
        "household_memory.py",
        "http_transport.py",
        "model_pipeline.py",
        "model_protocol.py",
        "observability.py",
        "privileged_service.py",
        "source_code.py",
        "sphere_tools.py",
        "tool_activity.py",
        "wearabllm_bridge.py",
        "requirements.txt",
    ]
    for name in files:
        shutil.copy2(BRIDGE_DIR / name, bridge_destination / name)
    source_bundle = build_source_bundle(destination)
    return [
        destination / "Dockerfile",
        destination / "README.md",
        source_bundle,
        *(bridge_destination / name for name in files),
    ]


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
