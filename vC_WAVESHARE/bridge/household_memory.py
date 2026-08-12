"""Service-role access to Sphere's provenance-rich household memory records."""

from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable


MEMORY_KINDS = {
    "preference",
    "person",
    "relationship",
    "household",
    "routine",
    "fact",
    "instruction",
}
WORD_RE = re.compile(r"[a-z0-9']+")
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 512


def _tokens(value: str) -> set[str]:
    return {token for token in WORD_RE.findall(value.lower()) if len(token) > 2}


def _clean_text(value: Any, *, name: str, minimum: int, maximum: int) -> str:
    cleaned = " ".join(str(value or "").split()).strip()
    if not minimum <= len(cleaned) <= maximum:
        raise ValueError(f"{name} must be {minimum}..{maximum} characters")
    return cleaned


class SupabaseHouseholdMemoryStore:
    """Small-record memory store mediated by the hosted Sphere bridge."""

    def __init__(
        self,
        url: str,
        service_role_key: str,
        *,
        principal_id: str = "primary",
        timeout: float = 15.0,
        embedding_provider: Callable[[str], list[float]] | None = None,
        embedding_model: str = EMBEDDING_MODEL,
        embedding_dimensions: int = EMBEDDING_DIMENSIONS,
    ) -> None:
        self.url = url.rstrip("/")
        self.service_role_key = service_role_key.strip()
        self.principal_id = principal_id.strip()
        self.timeout = timeout
        self.embedding_provider = embedding_provider
        self.embedding_model = embedding_model.strip()
        self.embedding_dimensions = int(embedding_dimensions)
        if not self.url.startswith("https://"):
            raise ValueError("SUPABASE_URL must use https://")
        if not self.service_role_key:
            raise ValueError("SUPABASE_SERVICE_ROLE_KEY is required")
        if not self.principal_id:
            raise ValueError("WEARABLLM_PRINCIPAL_ID is required")
        if not self.embedding_model:
            raise ValueError("Embedding model is required")
        if self.embedding_dimensions != EMBEDDING_DIMENSIONS:
            raise ValueError(f"Embedding dimensions must be {EMBEDDING_DIMENSIONS}")

    @property
    def hybrid_enabled(self) -> bool:
        return self.embedding_provider is not None

    @classmethod
    def from_environment(
        cls,
        *,
        embedding_provider: Callable[[str], list[float]] | None = None,
        embedding_model: str | None = None,
        embedding_dimensions: int | None = None,
    ) -> "SupabaseHouseholdMemoryStore":
        return cls(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            principal_id=os.environ.get("WEARABLLM_PRINCIPAL_ID", "primary"),
            embedding_provider=embedding_provider,
            embedding_model=embedding_model
            or os.environ.get("WEARABLLM_EMBEDDING_MODEL", EMBEDDING_MODEL),
            embedding_dimensions=(
                embedding_dimensions
                if embedding_dimensions is not None
                else int(
                    os.environ.get("WEARABLLM_EMBEDDING_DIMENSIONS", str(EMBEDDING_DIMENSIONS))
                )
            ),
        )

    def _embed(self, text: str) -> list[float] | None:
        if self.embedding_provider is None:
            return None
        raw = self.embedding_provider(text)
        if isinstance(raw, (str, bytes)) or len(raw) != self.embedding_dimensions:
            raise ValueError(f"Embedding must contain exactly {self.embedding_dimensions} numbers")
        vector = [float(value) for value in raw]
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("Embedding contains a non-finite value")
        return vector

    @staticmethod
    def _embedding_text(*, subject: str, kind: str, content: str, tags: list[str]) -> str:
        return "\n".join(
            (
                f"Subject: {subject}",
                f"Kind: {kind}",
                f"Memory: {content}",
                f"Tags: {', '.join(tags)}",
            )
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
            raise RuntimeError("Supabase returned invalid household-memory JSON") from exc

    def _active_records(self, *, limit: int = 500) -> list[dict[str, Any]]:
        principal = urllib.parse.quote(self.principal_id, safe="")
        safe_limit = max(1, min(int(limit), 1000))
        payload = self._request(
            "GET",
            "/rest/v1/wearabllm_memory_records"
            f"?principal_id=eq.{principal}&status=eq.active&select=*"
            f"&order=importance.desc,updated_at.desc&limit={safe_limit}",
        )
        now = datetime.now(timezone.utc)
        records: list[dict[str, Any]] = []
        for record in payload or []:
            if not isinstance(record, dict):
                continue
            expiry = record.get("expires_at")
            if expiry:
                try:
                    if datetime.fromisoformat(str(expiry).replace("Z", "+00:00")) <= now:
                        continue
                except ValueError:
                    continue
            records.append(record)
        return records

    def search(
        self,
        query: str,
        *,
        subject: str = "",
        kinds: list[str] | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        clean_query = _clean_text(query, name="query", minimum=1, maximum=500)
        clean_subject = " ".join(subject.split()).strip()
        clean_kinds = sorted({str(kind).strip().lower() for kind in kinds or []})
        if set(clean_kinds) - MEMORY_KINDS:
            raise ValueError("Unsupported memory kind")
        safe_limit = max(1, min(int(limit), 20))
        payload = self._request(
            "POST",
            "/rest/v1/rpc/wearabllm_search_memory",
            {
                "p_principal_id": self.principal_id,
                "p_query_text": clean_query,
                "p_query_embedding": self._embed(clean_query),
                "p_subject": clean_subject,
                "p_kinds": clean_kinds,
                "p_limit": safe_limit,
            },
        )
        return [self._public_record(record) for record in payload or [] if isinstance(record, dict)]

    def remember(
        self,
        *,
        subject: str,
        kind: str,
        content: str,
        tags: list[str] | None = None,
        importance: int = 3,
        confidence: float = 1.0,
        source_device_id: str = "",
        source_turn_id: int | None = None,
        expires_at: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        clean_subject = _clean_text(subject or "principal", name="subject", minimum=1, maximum=120)
        clean_kind = str(kind).strip().lower()
        if clean_kind not in MEMORY_KINDS:
            raise ValueError("Unsupported memory kind")
        clean_content = _clean_text(content, name="content", minimum=8, maximum=1200)
        for record in self._active_records():
            if str(record.get("content", "")).casefold() == clean_content.casefold():
                return self._public_record(record), False
        clean_tags = []
        for tag in tags or []:
            clean_tag = _clean_text(tag, name="tag", minimum=1, maximum=40).lower()
            if clean_tag not in clean_tags:
                clean_tags.append(clean_tag)
        clean_tags = clean_tags[:12]
        embedding = self._embed(
            self._embedding_text(
                subject=clean_subject,
                kind=clean_kind,
                content=clean_content,
                tags=clean_tags,
            )
        )
        payload: dict[str, Any] = {
            "principal_id": self.principal_id,
            "subject": clean_subject,
            "kind": clean_kind,
            "content": clean_content,
            "tags": clean_tags,
            "importance": max(1, min(int(importance), 5)),
            "confidence": max(0.0, min(float(confidence), 1.0)),
            "source": "wearabllm-explicit-tool",
            "source_device_id": source_device_id or None,
            "source_turn_id": source_turn_id,
            "last_confirmed_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires_at,
            "embedding": embedding,
            "embedding_model": self.embedding_model if embedding is not None else None,
            "embedded_at": datetime.now(timezone.utc).isoformat() if embedding is not None else None,
        }
        result = self._request("POST", "/rest/v1/wearabllm_memory_records", payload)
        if not isinstance(result, list) or not result or not isinstance(result[0], dict):
            raise RuntimeError("Supabase did not return the created memory")
        return self._public_record(result[0]), True

    def correct(
        self,
        memory_id: str,
        *,
        content: str,
        subject: str = "",
        kind: str = "",
        tags: list[str] | None = None,
        source_device_id: str = "",
    ) -> dict[str, Any]:
        clean_id = self._memory_id(memory_id)
        current = self.get(clean_id)
        if not current or current.get("status") != "active":
            raise LookupError("Active memory not found")
        clean_content = _clean_text(content, name="content", minimum=8, maximum=1200)
        if clean_content.casefold() == str(current.get("content", "")).casefold():
            raise ValueError("A correction must change the memory content")
        clean_kind = str(kind or current.get("kind", "fact")).strip().lower()
        if clean_kind not in MEMORY_KINDS:
            raise ValueError("Unsupported memory kind")
        clean_subject = _clean_text(
            subject or current.get("subject", "principal"),
            name="subject",
            minimum=1,
            maximum=120,
        )
        clean_tags = [
            _clean_text(tag, name="tag", minimum=1, maximum=40).lower()
            for tag in (tags if tags is not None else list(current.get("tags", [])))
        ][:12]
        embedding = self._embed(
            self._embedding_text(
                subject=clean_subject,
                kind=clean_kind,
                content=clean_content,
                tags=list(dict.fromkeys(clean_tags)),
            )
        )
        result = self._request(
            "POST",
            "/rest/v1/rpc/wearabllm_correct_memory",
            {
                "p_principal_id": self.principal_id,
                "p_memory_id": clean_id,
                "p_subject": clean_subject,
                "p_kind": clean_kind,
                "p_content": clean_content,
                "p_tags": list(dict.fromkeys(clean_tags)),
                "p_source_device_id": source_device_id,
                "p_embedding": embedding,
                "p_embedding_model": self.embedding_model if embedding is not None else None,
            },
        )
        if not isinstance(result, list) or not result or not isinstance(result[0], dict):
            raise RuntimeError("Supabase did not return the corrected memory")
        return self._public_record(result[0])

    def backfill_missing_embeddings(self, *, limit: int = 50) -> int:
        """Synchronously embed a bounded batch of this principal's legacy rows."""
        if self.embedding_provider is None:
            return 0
        principal = urllib.parse.quote(self.principal_id, safe="")
        safe_limit = max(1, min(int(limit), 100))
        payload = self._request(
            "GET",
            "/rest/v1/wearabllm_memory_records"
            f"?principal_id=eq.{principal}&status=eq.active&embedding=is.null&select=*"
            f"&order=updated_at.desc&limit={safe_limit}",
        )
        updated = 0
        for record in payload or []:
            if not isinstance(record, dict):
                continue
            memory_id = self._memory_id(str(record.get("id", "")))
            tags = [str(value) for value in record.get("tags", []) if value]
            vector = self._embed(
                self._embedding_text(
                    subject=str(record.get("subject", "principal")),
                    kind=str(record.get("kind", "fact")),
                    content=str(record.get("content", "")),
                    tags=tags,
                )
            )
            result = self._request(
                "PATCH",
                "/rest/v1/wearabllm_memory_records"
                f"?id=eq.{urllib.parse.quote(memory_id, safe='')}"
                f"&principal_id=eq.{principal}&embedding=is.null",
                {
                    "embedding": vector,
                    "embedding_model": self.embedding_model,
                    "embedded_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            if isinstance(result, list) and result:
                updated += 1
        return updated

    def forget(self, memory_id: str) -> dict[str, Any]:
        clean_id = self._memory_id(memory_id)
        current = self.get(clean_id)
        if not current:
            raise LookupError("Memory not found")
        if current.get("status") != "forgotten":
            result = self._request(
                "PATCH",
                f"/rest/v1/wearabllm_memory_records?id=eq.{urllib.parse.quote(clean_id, safe='')}",
                {"status": "forgotten"},
            )
            if isinstance(result, list) and result and isinstance(result[0], dict):
                current = result[0]
        return self._public_record(current)

    def get(self, memory_id: str) -> dict[str, Any] | None:
        clean_id = self._memory_id(memory_id)
        principal = urllib.parse.quote(self.principal_id, safe="")
        encoded_id = urllib.parse.quote(clean_id, safe="")
        payload = self._request(
            "GET",
            "/rest/v1/wearabllm_memory_records"
            f"?id=eq.{encoded_id}&principal_id=eq.{principal}&select=*&limit=1",
        )
        return payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], dict) else None

    @staticmethod
    def _memory_id(value: str) -> str:
        cleaned = str(value).strip().lower()
        if not re.fullmatch(r"[a-f0-9-]{36}", cleaned):
            raise ValueError("Invalid memory ID")
        return cleaned

    @staticmethod
    def _public_record(record: dict[str, Any]) -> dict[str, Any]:
        return {
            key: record.get(key)
            for key in (
                "id",
                "subject",
                "kind",
                "content",
                "tags",
                "importance",
                "confidence",
                "status",
                "source",
                "source_device_id",
                "source_turn_id",
                "supersedes_id",
                "created_at",
                "updated_at",
                "last_confirmed_at",
                "expires_at",
                "lexical_score",
                "semantic_score",
                "hybrid_score",
            )
        }
