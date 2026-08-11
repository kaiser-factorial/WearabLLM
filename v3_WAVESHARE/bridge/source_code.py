"""Read-only access to Sphere's build-time, explicitly published source bundle."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any


DEFAULT_SOURCE_BUNDLE = Path(__file__).resolve().with_name("source_bundle.json")
MAX_READ_LINES = 200
MAX_READ_CHARS = 30_000


def _safe_path(value: str, *, allow_root: bool = False) -> str:
    cleaned = str(value or "").strip().replace("\\", "/")
    if allow_root and cleaned in ("", "."):
        return ""
    path = PurePosixPath(cleaned)
    if not cleaned or path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("Source path must be a normalized relative path")
    return path.as_posix()


class SourceCodeStore:
    """Serve only files embedded in the deployment's opt-in source manifest."""

    def __init__(self, manifest_path: str | Path = DEFAULT_SOURCE_BUNDLE) -> None:
        self.manifest_path = Path(manifest_path)
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid Sphere source bundle: {exc}") from exc
        raw_files = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(raw_files, dict):
            raise ValueError("Sphere source bundle must contain a files object")
        files: dict[str, str] = {}
        for raw_path, content in raw_files.items():
            if not isinstance(raw_path, str) or not isinstance(content, str):
                raise ValueError("Sphere source bundle paths and contents must be strings")
            safe = _safe_path(raw_path)
            if safe != raw_path:
                raise ValueError(f"Sphere source bundle contains a non-normalized path: {raw_path}")
            files[safe] = content
        self.files = files

    def list(self, path: str = "", *, recursive: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        prefix = _safe_path(path, allow_root=True)
        prefix_with_slash = f"{prefix}/" if prefix else ""
        safe_limit = max(1, min(int(limit), 200))
        entries: dict[str, dict[str, Any]] = {}
        for file_path, content in self.files.items():
            if prefix and not file_path.startswith(prefix_with_slash):
                continue
            remaining = file_path[len(prefix_with_slash):]
            if not remaining:
                continue
            if recursive or "/" not in remaining:
                entry_path = file_path
                entries[entry_path] = self._file_entry(entry_path, content)
            else:
                child = remaining.split("/", 1)[0]
                entry_path = f"{prefix_with_slash}{child}"
                entries[entry_path] = {"path": entry_path, "type": "directory"}
        if prefix and not entries and prefix not in self.files:
            raise LookupError(f"Source directory not found: {prefix}")
        return [entries[key] for key in sorted(entries)[:safe_limit]]

    def read(self, path: str, *, start_line: int = 1, line_count: int = 120) -> dict[str, Any]:
        safe = _safe_path(path)
        if safe not in self.files:
            raise LookupError(f"Source file not found: {safe}")
        content = self.files[safe]
        lines = content.splitlines()
        total_lines = len(lines)
        safe_start = max(1, int(start_line))
        if safe_start > max(1, total_lines):
            raise ValueError(f"start_line exceeds the file's {total_lines} lines")
        safe_count = max(1, min(int(line_count), MAX_READ_LINES))
        selected = lines[safe_start - 1:safe_start - 1 + safe_count]
        chunk = "\n".join(selected)
        if len(chunk) > MAX_READ_CHARS:
            chunk = chunk[:MAX_READ_CHARS]
        actual_lines = chunk.count("\n") + (1 if chunk or selected else 0)
        end_line = safe_start + max(0, actual_lines - 1)
        return {
            "path": safe,
            "content": chunk,
            "start_line": safe_start,
            "end_line": end_line,
            "total_lines": total_lines,
            "truncated": end_line < total_lines or len("\n".join(selected)) > len(chunk),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }

    @staticmethod
    def _file_entry(path: str, content: str) -> dict[str, Any]:
        return {
            "path": path,
            "type": "file",
            "chars": len(content),
            "lines": len(content.splitlines()),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
