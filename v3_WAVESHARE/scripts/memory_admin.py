#!/usr/bin/env python3
"""Inspect or delete WearabLLM's private durable-memory records."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parents[1] / "bridge"
sys.path.insert(0, str(BRIDGE_DIR))

from durable_memory import (  # noqa: E402
    DEFAULT_MEMORY_FILE,
    DEFAULT_MEM_ROOT,
    DurableMemoryStore,
    MemDatabaseStore,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("local", "mem"), default="mem")
    parser.add_argument("--memory-file", default=str(DEFAULT_MEMORY_FILE))
    parser.add_argument("--mem-root", default=str(DEFAULT_MEM_ROOT))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list")
    forget = subparsers.add_parser("forget")
    forget.add_argument("text", help="Words that must all occur in records to delete")
    subparsers.add_parser("clear")
    args = parser.parse_args()

    store = (
        MemDatabaseStore(args.mem_root)
        if args.backend == "mem"
        else DurableMemoryStore(args.memory_file)
    )
    if args.command == "list":
        print(json.dumps(store.list(), indent=2))
    elif args.command == "forget":
        print(json.dumps({"removed": store.forget(args.text)}))
    elif args.command == "clear":
        print(json.dumps({"removed": store.clear()}))


if __name__ == "__main__":
    main()
