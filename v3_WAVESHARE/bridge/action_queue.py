"""Durable, device-targeted actions for the laptop-hosted WearabLLM bridge.

The ESP32 is an outbound-only client: it asks this queue for work and reports
its progress after rendering a response.  Keeping the queue in a small JSON
file makes the local/laptop deployment survive bridge restarts without making
Supabase a prerequisite for the first remote-presence loop.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEVICE_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,80}")
ACTION_ID_RE = re.compile(r"[a-f0-9-]{36}")
IDEMPOTENCY_RE = re.compile(r"[A-Za-z0-9._-]{1,120}")
VALID_STATUSES = {"queued", "dispatched", "delivered", "rendered", "tts_started", "played", "failed"}
TERMINAL_STATUSES = {"played", "failed"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_device_id(value: str) -> str:
    cleaned = value.strip()
    if not DEVICE_ID_RE.fullmatch(cleaned):
        raise ValueError("Invalid device ID")
    return cleaned


def validate_action_id(value: str) -> str:
    cleaned = value.strip().lower()
    if not ACTION_ID_RE.fullmatch(cleaned):
        raise ValueError("Invalid action ID")
    return cleaned


def validate_idempotency_key(value: str) -> str:
    cleaned = value.strip()
    if not IDEMPOTENCY_RE.fullmatch(cleaned):
        raise ValueError("Invalid idempotency key")
    return cleaned


class JsonActionQueue:
    """Thread-safe action queue persisted atomically to a local JSON file."""

    def __init__(self, path: str | Path, *, lease_seconds: int = 45, max_actions: int = 500) -> None:
        self.path = Path(path).expanduser()
        self.lease_seconds = max(5, min(int(lease_seconds), 300))
        self.max_actions = max(20, min(int(max_actions), 5000))
        self._lock = threading.RLock()
        self._actions = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read action queue {self.path}: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("actions"), list):
            raise RuntimeError(f"Invalid action queue format in {self.path}")
        return [item for item in payload["actions"] if isinstance(item, dict)]

    def _persist_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "actions": self._actions}
        fd, temporary_name = tempfile.mkstemp(prefix="actions-", suffix=".json", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    def _trim_locked(self) -> None:
        if len(self._actions) <= self.max_actions:
            return
        terminal = [item for item in self._actions if item.get("status") in TERMINAL_STATUSES]
        keep_terminal = terminal[-max(0, self.max_actions // 4):]
        nonterminal = [item for item in self._actions if item.get("status") not in TERMINAL_STATUSES]
        self._actions = [*keep_terminal, *nonterminal][-self.max_actions :]

    def create(
        self,
        *,
        origin_device_id: str,
        target_device_id: str,
        transcript: str,
        command: str,
        reply: str,
        idempotency_key: str = "",
    ) -> tuple[dict[str, Any], bool]:
        origin = validate_device_id(origin_device_id)
        target = validate_device_id(target_device_id)
        clean_transcript = transcript.strip()
        clean_command = command.strip().upper()
        clean_reply = reply.strip()
        if not clean_transcript:
            raise ValueError("Missing transcript")
        if not clean_command or len(clean_command) != 2:
            raise ValueError("Invalid command")
        if not clean_reply:
            raise ValueError("Missing reply")
        if len(clean_transcript) > 8000 or len(clean_reply) > 4000:
            raise ValueError("Action text is too long")
        key = validate_idempotency_key(idempotency_key) if idempotency_key else ""

        with self._lock:
            if key:
                existing = next(
                    (
                        item
                        for item in self._actions
                        if item.get("origin_device_id") == origin and item.get("idempotency_key") == key
                    ),
                    None,
                )
                if existing:
                    return deepcopy(existing), False
            timestamp = now_iso()
            action = {
                "id": str(uuid.uuid4()),
                "origin_device_id": origin,
                "target_device_id": target,
                "transcript": clean_transcript,
                "command": clean_command,
                "reply": clean_reply,
                "status": "queued",
                "idempotency_key": key or None,
                "attempts": 0,
                "created_at": timestamp,
                "updated_at": timestamp,
                "leased_until": None,
                "error": None,
            }
            self._actions.append(action)
            self._trim_locked()
            self._persist_locked()
            return deepcopy(action), True

    def claim_next(self, target_device_id: str) -> dict[str, Any] | None:
        target = validate_device_id(target_device_id)
        now = datetime.now(timezone.utc)
        with self._lock:
            candidate = next(
                (
                    item
                    for item in self._actions
                    if item.get("target_device_id") == target
                    and (
                        item.get("status") == "queued"
                        or (
                            item.get("status") == "dispatched"
                            and item.get("leased_until")
                            and parse_iso(str(item["leased_until"])) <= now
                        )
                    )
                ),
                None,
            )
            if not candidate:
                return None
            candidate["status"] = "dispatched"
            candidate["attempts"] = int(candidate.get("attempts", 0)) + 1
            candidate["leased_until"] = (now + timedelta(seconds=self.lease_seconds)).isoformat().replace("+00:00", "Z")
            candidate["updated_at"] = now_iso()
            self._persist_locked()
            return deepcopy(candidate)

    def acknowledge(self, target_device_id: str, action_id: str, status: str, error: str = "") -> dict[str, Any]:
        target = validate_device_id(target_device_id)
        action_key = validate_action_id(action_id)
        if status not in {"delivered", "rendered", "tts_started", "played", "failed"}:
            raise ValueError("Invalid action acknowledgement status")
        clean_error = error.strip()
        if len(clean_error) > 500:
            raise ValueError("Action error is too long")
        with self._lock:
            action = next((item for item in self._actions if item.get("id") == action_key), None)
            if not action or action.get("target_device_id") != target:
                raise LookupError("Action not found")
            current = str(action.get("status", ""))
            if current in TERMINAL_STATUSES and current != status:
                return deepcopy(action)
            if current == "played" and status == "played":
                return deepcopy(action)
            action["status"] = status
            action["leased_until"] = None
            action["updated_at"] = now_iso()
            action["error"] = clean_error or None
            self._persist_locked()
            return deepcopy(action)

    def get(self, action_id: str) -> dict[str, Any] | None:
        action_key = validate_action_id(action_id)
        with self._lock:
            action = next((item for item in self._actions if item.get("id") == action_key), None)
            return deepcopy(action) if action else None

    def list(self, *, target_device_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        target = validate_device_id(target_device_id) if target_device_id else ""
        safe_limit = max(1, min(int(limit), 500))
        with self._lock:
            actions = [item for item in self._actions if not target or item.get("target_device_id") == target]
            return deepcopy(actions[-safe_limit:][::-1])


class SupabaseActionQueue:
    """Service-role-only device action queue shared by every hosted client."""

    def __init__(
        self,
        url: str,
        service_role_key: str,
        *,
        principal_id: str = "primary",
        lease_seconds: int = 45,
        timeout: float = 15.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.service_role_key = service_role_key.strip()
        self.principal_id = principal_id.strip()
        self.lease_seconds = max(5, min(int(lease_seconds), 300))
        self.timeout = timeout
        if not self.url.startswith("https://"):
            raise ValueError("SUPABASE_URL must use https://")
        if not self.service_role_key:
            raise ValueError("SUPABASE_SERVICE_ROLE_KEY is required")
        if not self.principal_id:
            raise ValueError("WEARABLLM_PRINCIPAL_ID is required")

    @classmethod
    def from_environment(cls, *, lease_seconds: int = 45) -> "SupabaseActionQueue":
        return cls(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            principal_id=os.environ.get("WEARABLLM_PRINCIPAL_ID", "primary"),
            lease_seconds=lease_seconds,
        )

    def _request(self, method: str, path: str, payload: Any | None = None) -> Any:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.url}{path}",
            data=body,
            method=method,
            headers={
                "apikey": self.service_role_key,
                "Authorization": f"Bearer {self.service_role_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Prefer": "return=representation",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Supabase {method} {path} failed ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Supabase {method} {path} failed: {exc.reason}") from exc
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Supabase returned invalid action JSON") from exc

    def _row(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(record.get("id", "")),
            "origin_device_id": str(record.get("origin_device_id", "")),
            "target_device_id": str(record.get("target_device_id", "")),
            "transcript": str(record.get("transcript", "")),
            "command": str(record.get("command", "")),
            "reply": str(record.get("reply", "")),
            "status": str(record.get("status", "queued")),
            "idempotency_key": record.get("idempotency_key"),
            "attempts": int(record.get("delivery_attempts", 0)),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "leased_until": record.get("lease_expires_at"),
            "error": record.get("error"),
        }

    def _find_idempotent(self, origin: str, key: str) -> dict[str, Any] | None:
        principal = urllib.parse.quote(self.principal_id, safe="")
        encoded_origin = urllib.parse.quote(origin, safe="")
        encoded_key = urllib.parse.quote(key, safe="")
        payload = self._request(
            "GET",
            "/rest/v1/wearabllm_device_actions"
            f"?principal_id=eq.{principal}&origin_device_id=eq.{encoded_origin}"
            f"&idempotency_key=eq.{encoded_key}&select=*&limit=1",
        )
        return self._row(payload[0]) if isinstance(payload, list) and payload else None

    def create(
        self,
        *,
        origin_device_id: str,
        target_device_id: str,
        transcript: str,
        command: str,
        reply: str,
        idempotency_key: str = "",
    ) -> tuple[dict[str, Any], bool]:
        origin = validate_device_id(origin_device_id)
        target = validate_device_id(target_device_id)
        clean_transcript = transcript.strip()
        clean_command = command.strip().upper()
        clean_reply = reply.strip()
        key = validate_idempotency_key(idempotency_key) if idempotency_key else ""
        if not clean_transcript:
            raise ValueError("Missing transcript")
        if not clean_command or len(clean_command) != 2:
            raise ValueError("Invalid command")
        if not clean_reply:
            raise ValueError("Missing reply")
        if len(clean_transcript) > 4000 or len(clean_reply) > 8000:
            raise ValueError("Action text is too long")
        if key:
            existing = self._find_idempotent(origin, key)
            if existing:
                return existing, False
        payload = self._request(
            "POST",
            "/rest/v1/wearabllm_device_actions",
            {
                "principal_id": self.principal_id,
                "origin_device_id": origin,
                "target_device_id": target,
                "transcript": clean_transcript,
                "command": clean_command,
                "reply": clean_reply,
                "status": "queued",
                "idempotency_key": key or None,
            },
        )
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise RuntimeError("Supabase did not return the created action")
        return self._row(payload[0]), True

    def claim_next(self, target_device_id: str) -> dict[str, Any] | None:
        target = validate_device_id(target_device_id)
        payload = self._request(
            "POST",
            "/rest/v1/rpc/wearabllm_claim_next_device_action",
            {
                "p_principal_id": self.principal_id,
                "p_target_device_id": target,
                "p_lease_seconds": self.lease_seconds,
            },
        )
        return self._row(payload[0]) if isinstance(payload, list) and payload else None

    def acknowledge(self, target_device_id: str, action_id: str, status: str, error: str = "") -> dict[str, Any]:
        target = validate_device_id(target_device_id)
        action_key = validate_action_id(action_id)
        if status not in {"delivered", "rendered", "tts_started", "played", "failed"}:
            raise ValueError("Invalid action acknowledgement status")
        clean_error = error.strip()
        if len(clean_error) > 500:
            raise ValueError("Action error is too long")
        existing = self.get(action_key)
        if not existing or existing.get("target_device_id") != target:
            raise LookupError("Action not found")
        current = str(existing.get("status", ""))
        if current in TERMINAL_STATUSES:
            return existing
        update: dict[str, Any] = {
            "status": status,
            "lease_expires_at": None,
            "error": clean_error or None,
        }
        timestamp = now_iso()
        if status == "delivered":
            update["delivered_at"] = timestamp
        elif status == "played":
            update["played_at"] = timestamp
        elif status == "failed":
            update["failed_at"] = timestamp
        encoded_id = urllib.parse.quote(action_key, safe="")
        principal = urllib.parse.quote(self.principal_id, safe="")
        payload = self._request(
            "PATCH",
            f"/rest/v1/wearabllm_device_actions?id=eq.{encoded_id}&principal_id=eq.{principal}",
            update,
        )
        if not isinstance(payload, list) or not payload:
            raise LookupError("Action not found")
        return self._row(payload[0])

    def get(self, action_id: str) -> dict[str, Any] | None:
        action_key = validate_action_id(action_id)
        encoded_id = urllib.parse.quote(action_key, safe="")
        principal = urllib.parse.quote(self.principal_id, safe="")
        payload = self._request(
            "GET",
            "/rest/v1/wearabllm_device_actions"
            f"?id=eq.{encoded_id}&principal_id=eq.{principal}&select=*&limit=1",
        )
        return self._row(payload[0]) if isinstance(payload, list) and payload else None

    def list(self, *, target_device_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        target = validate_device_id(target_device_id) if target_device_id else ""
        safe_limit = max(1, min(int(limit), 500))
        principal = urllib.parse.quote(self.principal_id, safe="")
        target_filter = (
            f"&target_device_id=eq.{urllib.parse.quote(target, safe='')}" if target else ""
        )
        payload = self._request(
            "GET",
            "/rest/v1/wearabllm_device_actions"
            f"?principal_id=eq.{principal}{target_filter}&select=*"
            f"&order=created_at.desc&limit={safe_limit}",
        )
        return [self._row(record) for record in payload or [] if isinstance(record, dict)]
