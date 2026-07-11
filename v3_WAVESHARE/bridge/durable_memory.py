"""Local, durable user-memory support for the WearabLLM bridge."""

from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_MEMORY_FILE = Path.home() / ".wearabllm" / "memory.json"
DEFAULT_MEM_ROOT = Path.home() / "Projects" / "MEMORY"
WORD_RE = re.compile(r"[a-z0-9']+")
SENSITIVE_RE = re.compile(
    r"\b(?:password|passcode|api[_ -]?key|access[_ -]?token|auth(?:entication)?[_ -]?token|"
    r"private[_ -]?key|secret|social security|ssn|credit card|routing number|bank account)\b|"
    r"\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b|"
    r"\b\d{1,5}\s+[a-z0-9.' -]+\s(?:street|st|road|rd|avenue|ave|boulevard|blvd|lane|ln)\b",
    re.IGNORECASE,
)

EXTRACTION_PROMPT = """Extract durable user memories from this conversation turn.

Return only a JSON array of strings. Return [] when nothing should be saved.
Save concise, standalone facts likely to remain useful across future conversations,
such as the user's identity, stable preferences, long-running projects, recurring
routines, important relationships, or stated goals.

Do not save raw conversation text, assistant claims, guesses, one-off requests,
temporary moods, or facts that are only relevant to the current task. Never save
passwords, API keys, authentication data, precise financial data, precise location,
or highly sensitive medical/legal details. A correction should be phrased as the
new current fact. Produce at most 3 memories.
"""


def parse_memory_candidates(raw: str) -> list[str]:
    """Parse and sanitize the model's JSON-array extraction response."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", text)
        if not match:
            return []
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []

    candidates: list[str] = []
    for item in value[:3]:
        if not isinstance(item, str):
            continue
        candidate = " ".join(item.split()).strip()
        if (
            8 <= len(candidate) <= 400
            and not SENSITIVE_RE.search(candidate)
            and candidate not in candidates
        ):
            candidates.append(candidate)
    return candidates


def _tokens(text: str) -> set[str]:
    return {token for token in WORD_RE.findall(text.lower()) if len(token) > 2}


class DurableMemoryStore:
    """Small private JSON store with atomic writes and lexical retrieval."""

    def __init__(self, path: str | Path = DEFAULT_MEMORY_FILE) -> None:
        self.path = Path(path).expanduser()
        self.lock = threading.RLock()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        records = payload.get("memories", []) if isinstance(payload, dict) else []
        return [record for record in records if isinstance(record, dict)]

    def _save(self, records: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": 1, "memories": records}, indent=2) + "\n"
        fd, temp_name = tempfile.mkstemp(prefix="memory-", suffix=".json", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
                temp_file.write(payload)
            os.replace(temp_name, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def add(self, content: str) -> bool:
        normalized = " ".join(content.split()).strip()
        if not normalized:
            return False
        with self.lock:
            records = self._load()
            key = normalized.casefold()
            for record in records:
                if str(record.get("content", "")).casefold() == key:
                    record["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                    self._save(records)
                    return False
            now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            records.append({
                "id": str(uuid.uuid4()),
                "content": normalized,
                "created_at": now,
                "updated_at": now,
                "source": "wearabllm-auto-extract",
            })
            self._save(records)
            return True

    def retrieve(self, query: str, limit: int = 3) -> list[str]:
        query_tokens = _tokens(query)
        if not query_tokens or limit <= 0:
            return []
        with self.lock:
            records = self._load()
        scored: list[tuple[float, str]] = []
        for record in records:
            content = str(record.get("content", "")).strip()
            memory_tokens = _tokens(content)
            overlap = query_tokens & memory_tokens
            if not overlap:
                continue
            score = len(overlap) / max(1, len(query_tokens | memory_tokens))
            scored.append((score, content))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [content for _, content in scored[:limit]]

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            return self._load()

    def forget(self, text: str) -> int:
        terms = _tokens(text)
        if not terms:
            return 0
        with self.lock:
            records = self._load()
            kept = [
                record for record in records
                if not terms.issubset(_tokens(str(record.get("content", ""))))
            ]
            removed = len(records) - len(kept)
            if removed:
                self._save(kept)
            return removed

    def clear(self) -> int:
        with self.lock:
            records = self._load()
            if records:
                self._save([])
            return len(records)


class MemDatabaseStore:
    """Adapter for the shared MEM database's machine-readable CLI."""

    def __init__(
        self,
        root: str | Path = DEFAULT_MEM_ROOT,
        *,
        source: str = "wearabllm-home-assistant",
        tags: tuple[str, ...] = ("personal", "wearabllm", "assistant-memory"),
        timeout: float = 30.0,
    ) -> None:
        self.root = Path(root).expanduser()
        self.cli = self.root / "bin" / "mem.js"
        self.source = source
        self.tags = tags
        self.timeout = timeout
        if not self.cli.exists():
            raise FileNotFoundError(f"MEM CLI not found: {self.cli}")

    def _run(self, *args: str) -> dict[str, Any]:
        result = subprocess.run(
            ["node", str(self.cli), *args, "--json"],
            cwd=self.root,
            text=True,
            capture_output=True,
            timeout=self.timeout,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(detail or f"MEM exited with status {result.returncode}")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("MEM returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("MEM returned a non-object response")
        return payload

    def add(self, content: str) -> bool:
        normalized = " ".join(content.split()).strip()
        if not normalized:
            return False
        memory_key = hashlib.sha256(normalized.casefold().encode("utf-8")).hexdigest()[:24]
        payload = self._run(
            "upsert",
            normalized,
            "--key",
            memory_key,
            "--source",
            self.source,
            "--tags",
            ",".join(self.tags),
            "--local-only",
        )
        return payload.get("success") is True

    def retrieve(self, query: str, limit: int = 3) -> list[str]:
        if not query.strip() or limit <= 0:
            return []
        payload = self._run(
            "query",
            query,
            "--source",
            self.source,
            "--tags",
            "personal",
            "--limit",
            str(limit),
            "--minScore",
            "0.3",
        )
        results = payload.get("results", [])
        return [
            str(record.get("content", "")).strip()
            for record in results
            if isinstance(record, dict) and str(record.get("content", "")).strip()
        ]

    def list(self) -> list[dict[str, Any]]:
        payload = self._run("list", "--limit", "10000")
        records = payload.get("memories", [])
        return [
            record for record in records
            if isinstance(record, dict) and record.get("source") == self.source
        ]

    def forget(self, text: str) -> int:
        terms = _tokens(text)
        if not terms:
            return 0
        removed = 0
        for record in self.list():
            if terms.issubset(_tokens(str(record.get("content", "")))):
                memory_id = str(record.get("id", ""))
                if memory_id and self._run("delete", memory_id).get("success") is True:
                    removed += 1
        return removed

    def clear(self) -> int:
        records = self.list()
        removed = 0
        for record in records:
            memory_id = str(record.get("id", ""))
            if memory_id and self._run("delete", memory_id).get("success") is True:
                removed += 1
        return removed


class SupabaseMemoryStore:
    """Private Supabase/PostgREST durable memory for the hosted agent.

    The Space holds the service-role key as a secret. Devices never receive the
    key and authenticate only to the bridge. Retrieval intentionally uses the
    same small lexical scorer as the local fallback until semantic embeddings
    are added to the hosted memory schema.
    """

    def __init__(
        self,
        url: str,
        service_role_key: str,
        *,
        principal_id: str = "primary",
        timeout: float = 15.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.service_role_key = service_role_key.strip()
        self.principal_id = principal_id.strip()
        self.timeout = timeout
        if not self.url.startswith("https://"):
            raise ValueError("SUPABASE_URL must use https://")
        if not self.service_role_key:
            raise ValueError("SUPABASE_SERVICE_ROLE_KEY is required")
        if not self.principal_id:
            raise ValueError("WEARABLLM_PRINCIPAL_ID is required")

    @classmethod
    def from_environment(cls) -> "SupabaseMemoryStore":
        return cls(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            principal_id=os.environ.get("WEARABLLM_PRINCIPAL_ID", "primary"),
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
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
            raise RuntimeError("Supabase returned invalid JSON") from exc

    def _records(self) -> list[dict[str, Any]]:
        principal = urllib.parse.quote(self.principal_id, safe="")
        payload = self._request(
            "GET",
            "/rest/v1/wearabllm_memories"
            f"?principal_id=eq.{principal}&select=id,content,memory_key,created_at,updated_at"
            "&order=updated_at.desc&limit=1000",
        )
        return [record for record in payload or [] if isinstance(record, dict)]

    def add(self, content: str) -> bool:
        normalized = " ".join(content.split()).strip()
        if not normalized:
            return False
        memory_key = hashlib.sha256(normalized.casefold().encode("utf-8")).hexdigest()[:24]
        existing = next(
            (record for record in self._records() if record.get("memory_key") == memory_key),
            None,
        )
        if existing:
            memory_id = urllib.parse.quote(str(existing["id"]), safe="")
            self._request(
                "PATCH",
                f"/rest/v1/wearabllm_memories?id=eq.{memory_id}",
                {"updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
            )
            return False
        self._request(
            "POST",
            "/rest/v1/wearabllm_memories",
            {
                "principal_id": self.principal_id,
                "memory_key": memory_key,
                "content": normalized,
                "source": "wearabllm-auto-extract",
            },
        )
        return True

    def retrieve(self, query: str, limit: int = 3) -> list[str]:
        query_tokens = _tokens(query)
        if not query_tokens or limit <= 0:
            return []
        scored: list[tuple[float, str]] = []
        for record in self._records():
            content = str(record.get("content", "")).strip()
            memory_tokens = _tokens(content)
            overlap = query_tokens & memory_tokens
            if overlap:
                scored.append((len(overlap) / max(1, len(query_tokens | memory_tokens)), content))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [content for _, content in scored[:limit]]

    def list(self) -> list[dict[str, Any]]:
        return self._records()

    def forget(self, text: str) -> int:
        terms = _tokens(text)
        if not terms:
            return 0
        removed = 0
        for record in self._records():
            if terms.issubset(_tokens(str(record.get("content", "")))):
                memory_id = urllib.parse.quote(str(record["id"]), safe="")
                self._request("DELETE", f"/rest/v1/wearabllm_memories?id=eq.{memory_id}")
                removed += 1
        return removed

    def clear(self) -> int:
        records = self._records()
        principal = urllib.parse.quote(self.principal_id, safe="")
        self._request("DELETE", f"/rest/v1/wearabllm_memories?principal_id=eq.{principal}")
        return len(records)


class SupabaseConversationStore:
    """Shared active sessions plus private long-term raw conversation archive."""

    def __init__(
        self,
        url: str,
        service_role_key: str,
        *,
        principal_id: str = "primary",
        timeout: float = 15.0,
        session_idle_seconds: int = 3600,
    ) -> None:
        self.url = url.rstrip("/")
        self.service_role_key = service_role_key.strip()
        self.principal_id = principal_id.strip()
        self.timeout = timeout
        self.session_idle_seconds = max(60, int(session_idle_seconds))
        if not self.url.startswith("https://"):
            raise ValueError("SUPABASE_URL must use https://")
        if not self.service_role_key:
            raise ValueError("SUPABASE_SERVICE_ROLE_KEY is required")
        if not self.principal_id:
            raise ValueError("WEARABLLM_PRINCIPAL_ID is required")

    @classmethod
    def from_environment(cls, *, session_idle_seconds: int | None = None) -> "SupabaseConversationStore":
        return cls(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            principal_id=os.environ.get("WEARABLLM_PRINCIPAL_ID", "primary"),
            session_idle_seconds=session_idle_seconds
            if session_idle_seconds is not None
            else int(os.environ.get("WEARABLLM_SESSION_IDLE_SECONDS", "3600")),
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: Any | None = None,
    ) -> Any:
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
            raise RuntimeError("Supabase returned invalid JSON") from exc

    def active_session(self) -> dict[str, Any] | None:
        principal = urllib.parse.quote(self.principal_id, safe="")
        payload = self._request(
            "GET",
            "/rest/v1/wearabllm_conversation_sessions"
            f"?principal_id=eq.{principal}&ended_at=is.null"
            "&select=id,started_at,last_turn_at&order=started_at.desc&limit=1",
        )
        return payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], dict) else None

    def session_expired(self, session: dict[str, Any]) -> bool:
        raw = str(session.get("last_turn_at") or session.get("started_at") or "")
        try:
            last_turn_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return True
        return datetime.now(timezone.utc) - last_turn_at > timedelta(seconds=self.session_idle_seconds)

    def create_session(self) -> dict[str, Any]:
        payload = self._request(
            "POST",
            "/rest/v1/wearabllm_conversation_sessions",
            {"principal_id": self.principal_id},
        )
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise RuntimeError("Supabase did not return a conversation session")
        return payload[0]

    def history(self, session_id: str, limit: int) -> list[dict[str, str]]:
        if limit <= 0:
            return []
        encoded_session_id = urllib.parse.quote(session_id, safe="")
        payload = self._request(
            "GET",
            "/rest/v1/wearabllm_conversation_turns"
            f"?session_id=eq.{encoded_session_id}&select=id,role,content,created_at"
            f"&order=created_at.desc,id.desc&limit={limit}",
        )
        records = payload if isinstance(payload, list) else []
        messages = [
            {"role": str(record["role"]), "content": str(record["content"])}
            for record in reversed(records)
            if isinstance(record, dict)
            and record.get("role") in ("user", "assistant")
            and str(record.get("content", "")).strip()
        ]
        return messages

    def turns(self, session_id: str) -> list[dict[str, Any]]:
        encoded_session_id = urllib.parse.quote(session_id, safe="")
        payload = self._request(
            "GET",
            "/rest/v1/wearabllm_conversation_turns"
            f"?session_id=eq.{encoded_session_id}&select=id,device_id,role,content,created_at"
            "&order=created_at.asc,id.asc&limit=10000",
        )
        return [record for record in payload or [] if isinstance(record, dict)]

    def append(self, session_id: str, device_id: str, role: str, content: str) -> None:
        normalized_device_id = " ".join(device_id.split()).strip()
        normalized_content = " ".join(content.split()).strip()
        if role not in ("user", "assistant"):
            raise ValueError("Conversation role must be user or assistant")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", normalized_device_id):
            raise ValueError("Conversation device ID is invalid")
        if not normalized_content:
            raise ValueError("Conversation content is required")
        self._request(
            "POST",
            "/rest/v1/wearabllm_conversation_turns",
            {
                "principal_id": self.principal_id,
                "session_id": session_id,
                "device_id": normalized_device_id,
                "role": role,
                "content": normalized_content,
            },
        )
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        encoded_session_id = urllib.parse.quote(session_id, safe="")
        self._request(
            "PATCH",
            f"/rest/v1/wearabllm_conversation_sessions?id=eq.{encoded_session_id}",
            {"last_turn_at": now},
        )

    def archive(self, session: dict[str, Any], summary: str | None = None) -> int:
        session_id = str(session.get("id", "")).strip()
        if not session_id:
            raise ValueError("Conversation session is missing an ID")
        records = self.turns(session_id)
        if records:
            archive_records = [
                {
                    "original_turn_id": record["id"],
                    "session_id": session_id,
                    "principal_id": self.principal_id,
                    "device_id": record["device_id"],
                    "role": record["role"],
                    "content": record["content"],
                    "created_at": record["created_at"],
                }
                for record in records
            ]
            self._request("POST", "/rest/v1/wearabllm_conversation_archive", archive_records)
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        encoded_session_id = urllib.parse.quote(session_id, safe="")
        self._request(
            "PATCH",
            f"/rest/v1/wearabllm_conversation_sessions?id=eq.{encoded_session_id}",
            {"ended_at": now, "archived_at": now, "summary": summary or None},
        )
        self._request("DELETE", f"/rest/v1/wearabllm_conversation_turns?session_id=eq.{encoded_session_id}")
        return len(records)

    def clear(self) -> int:
        session = self.active_session()
        return self.archive(session) if session else 0
