"""Privacy-safe structured logging for the WearabLLM bridge.

The default event path is deliberately metadata-only. Event producers must use
the small allowlist below; request or response content, credentials, Wi-Fi
identifiers, memory contents, and raw exception messages are rejected by field
name. A separate, explicitly enabled local debug helper exists for transcript
and reply troubleshooting and must never be enabled in the hosted bridge.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any


REQUEST_ID_HEADER = "X-Request-Id"
REQUEST_ID_RE = re.compile(r"[a-f0-9]{32}")
EVENT_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,79}")

ALLOWED_METADATA_FIELDS = {
    "action_status",
    "action_type",
    "backend",
    "command",
    "count",
    "debug_content_logs",
    "device_id",
    "duration_ms",
    "enabled",
    "error_code",
    "error_type",
    "host",
    "method",
    "model",
    "operation",
    "outcome",
    "port",
    "provider",
    "request_bytes",
    "request_id",
    "response_bytes",
    "route",
    "saved_capture",
    "status",
}
SENSITIVE_FIELD_MARKERS = {
    "authorization",
    "content",
    "credential",
    "key",
    "memory",
    "password",
    "prompt",
    "raw",
    "reply",
    "secret",
    "ssid",
    "text",
    "token",
    "transcript",
}
SAFE_STRING_RE = re.compile(r"[\x20-\x7e]{0,160}")


def new_request_id() -> str:
    return uuid.uuid4().hex


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _validate_metadata(fields: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for name, value in fields.items():
        lowered = name.lower()
        if name not in ALLOWED_METADATA_FIELDS or (
            name != "debug_content_logs"
            and any(marker in lowered for marker in SENSITIVE_FIELD_MARKERS)
        ):
            raise ValueError(f"Unsafe structured-log field: {name}")
        if value is None:
            continue
        if isinstance(value, bool):
            sanitized[name] = value
            continue
        if isinstance(value, int):
            sanitized[name] = value
            continue
        if isinstance(value, float):
            sanitized[name] = round(value, 3)
            continue
        if isinstance(value, str) and SAFE_STRING_RE.fullmatch(value):
            sanitized[name] = value
            continue
        raise ValueError(f"Unsafe structured-log value for: {name}")
    return sanitized


def emit_event(
    event: str,
    *,
    level: str = "info",
    sink: Callable[[str], None] | None = None,
    **fields: Any,
) -> None:
    """Write one allowlisted JSON event.

    ``sink`` is injectable for tests. The normal runtime writes one compact JSON
    object per line to stdout, which Hugging Face captures without requiring a
    second persistence path during Phase 0.
    """

    if not EVENT_NAME_RE.fullmatch(event):
        raise ValueError("Invalid structured-log event name")
    if level not in {"debug", "info", "warning", "error"}:
        raise ValueError("Invalid structured-log level")
    payload = {
        "timestamp": _now_iso(),
        "level": level,
        "event": event,
        **_validate_metadata(fields),
    }
    line = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if sink is None:
        print(line, flush=True)
    else:
        sink(line)


def emit_exception(
    event: str,
    exc: BaseException,
    *,
    level: str = "error",
    sink: Callable[[str], None] | None = None,
    **fields: Any,
) -> None:
    """Record only an exception class, never its possibly sensitive message."""

    emit_event(event, level=level, sink=sink, error_type=type(exc).__name__, **fields)


def emit_debug_content(
    event: str,
    *,
    transcript: str = "",
    reply: str = "",
    tts_text: str = "",
    sink: Callable[[str], None] | None = None,
) -> None:
    """Emit explicitly opted-in local content diagnostics.

    This function intentionally bypasses the metadata allowlist so the privacy
    boundary is visible in code review. Callers must guard it with the local
    ``--debug-content-logs`` flag. Credentials, Wi-Fi values, and authorization
    headers are never accepted here.
    """

    if not EVENT_NAME_RE.fullmatch(event):
        raise ValueError("Invalid structured-log event name")
    payload = {
        "timestamp": _now_iso(),
        "level": "debug",
        "event": event,
        "privacy": "content",
    }
    if transcript:
        payload["transcript"] = transcript
    if reply:
        payload["reply"] = reply
    if tts_text:
        payload["tts_text"] = tts_text
    line = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if sink is None:
        print(line, flush=True)
    else:
        sink(line)
