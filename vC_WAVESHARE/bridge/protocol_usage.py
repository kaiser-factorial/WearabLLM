"""Privacy-safe aggregate protocol usage for migration evidence.

Only bounded operational dimensions are retained. Raw paths, device IDs,
request IDs, query parameters, content, credentials, and payload sizes never
enter this store.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping


CLIENT_NAME_HEADER = "X-WearabLLM-Client"
CLIENT_VERSION_HEADER = "X-WearabLLM-Client-Version"
CLIENT_NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,39}")
CLIENT_VERSION_RE = re.compile(
    r"(?:unknown|[0-9]{1,4}(?:\.[0-9]{1,4}){0,3}(?:[-+][A-Za-z0-9.-]{1,20})?)"
)
KNOWN_CLIENT_NAMES = frozenset(
    {
        "android",
        "bench-doctor",
        "bench-smoke",
        "preflight",
        "waveshare",
        "web-console",
        "unknown",
    }
)
ROUTE_FAMILY_RE = re.compile(r"[a-z][a-z0-9_]{0,39}")
STATUS_CLASS_RE = re.compile(r"[1-5]xx")
MAX_SNAPSHOT_DAYS = 90
MAX_SNAPSHOT_ROWS = 2_000


ENDPOINT_ROUTE_FAMILIES = {
    "_handle_health": "health",
    "_handle_get_admin_config": "admin_config",
    "_handle_admin_catalog": "admin_catalog",
    "_handle_protocol_usage": "protocol_usage",
    "_handle_list_interactions": "interaction_list",
    "_handle_get_interaction": "interaction_status",
    "_handle_interaction": "interaction_create",
    "_handle_claim_action": "action_claim",
    "_handle_action_ack": "action_ack",
    "_handle_list_sensors": "sensor_list",
    "_handle_sensor_manifest": "sensor_manifest",
    "_handle_conversation_snapshot": "conversation",
    "_handle_list_devices": "device_list",
    "_handle_list_sessions": "session_list",
    "_handle_session_reset": "session_reset",
    "_handle_session_archive": "session_archive",
    "_handle_session_rename": "session_rename",
    "_handle_audio_query": "query_audio",
    "_handle_text_query": "query_text",
    "_handle_tts": "tts",
    "_handle_heartbeat": "heartbeat",
    "_handle_device_wifi": "device_config",
    "_handle_admin_config": "admin_config",
    "_handle_admin_api_key": "admin_api_key",
}


def normalize_client_identity(name: str, version: str) -> tuple[str, str]:
    """Return bounded explicit identity values or the fail-closed fallback."""
    clean_name = name.strip().lower()
    clean_version = version.strip()
    if not CLIENT_NAME_RE.fullmatch(clean_name) or clean_name not in KNOWN_CLIENT_NAMES:
        return "unknown", "unknown"
    if not CLIENT_VERSION_RE.fullmatch(clean_version):
        clean_version = "unknown"
    return clean_name, clean_version


def route_family(endpoint: str | None) -> str:
    family = ENDPOINT_ROUTE_FAMILIES.get(endpoint or "", "unknown")
    return family if ROUTE_FAMILY_RE.fullmatch(family) else "unknown"


def status_class(status: int) -> str:
    candidate = f"{int(status) // 100}xx"
    return candidate if STATUS_CLASS_RE.fullmatch(candidate) else "5xx"


@dataclass(frozen=True, slots=True)
class UsageKey:
    day: str
    protocol_version: str
    route_family: str
    method: str
    status_class: str
    client_name: str
    client_version: str

    def public_dict(self, count: int) -> dict[str, Any]:
        return {
            "day": self.day,
            "protocol_version": self.protocol_version,
            "route_family": self.route_family,
            "method": self.method,
            "status_class": self.status_class,
            "client_name": self.client_name,
            "client_version": self.client_version,
            "request_count": int(count),
        }


class ProtocolUsageRecorder:
    """Non-blocking aggregate recorder with optional durable Supabase flushes."""

    def __init__(
        self,
        *,
        principal_id: str = "primary",
        supabase_url: str = "",
        supabase_service_role_key: str = "",
        flush_interval_seconds: float = 60.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.principal_id = principal_id
        self.supabase_url = supabase_url.rstrip("/")
        self.supabase_service_role_key = supabase_service_role_key
        self.flush_interval_seconds = max(1.0, float(flush_interval_seconds))
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._pending: dict[UsageKey, int] = defaultdict(int)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        if self.durable:
            self._worker = threading.Thread(
                target=self._flush_loop,
                name="wearabllm-protocol-usage",
                daemon=True,
            )
            self._worker.start()

    @property
    def durable(self) -> bool:
        return bool(self.supabase_url.startswith("https://") and self.supabase_service_role_key)

    @property
    def backend(self) -> str:
        return "supabase-aggregate" if self.durable else "memory-aggregate"

    def record(
        self,
        *,
        protocol_version: int,
        route_family_value: str,
        method: str,
        status: int,
        client_name: str,
        client_version: str,
    ) -> None:
        name, version = normalize_client_identity(client_name, client_version)
        family = route_family_value if ROUTE_FAMILY_RE.fullmatch(route_family_value) else "unknown"
        key = UsageKey(
            day=self._now().astimezone(timezone.utc).date().isoformat(),
            protocol_version="v2" if protocol_version == 2 else "v1",
            route_family=family,
            method=method if method in {"GET", "POST", "OPTIONS"} else "OTHER",
            status_class=status_class(status),
            client_name=name,
            client_version=version,
        )
        with self._lock:
            self._pending[key] += 1

    def pending_count(self) -> int:
        with self._lock:
            return sum(self._pending.values())

    def _take_pending(self, limit: int = 500) -> dict[UsageKey, int]:
        with self._lock:
            keys = list(self._pending)[:limit]
            batch = {key: self._pending.pop(key) for key in keys}
        return batch

    def _restore_pending(self, batch: Mapping[UsageKey, int]) -> None:
        with self._lock:
            for key, count in batch.items():
                self._pending[key] += count

    def _request(self, method: str, path: str, payload: Any | None = None) -> Any:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.supabase_url}{path}",
            data=body,
            method=method,
            headers={
                "apikey": self.supabase_service_role_key,
                "Authorization": f"Bearer {self.supabase_service_role_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else None

    def flush(self) -> int:
        if not self.durable:
            return 0
        flushed = 0
        while True:
            batch = self._take_pending()
            if not batch:
                return flushed
            rows = [key.public_dict(count) for key, count in batch.items()]
            try:
                self._request(
                    "POST",
                    "/rest/v1/rpc/wearabllm_increment_protocol_usage",
                    {"p_principal_id": self.principal_id, "p_rows": rows},
                )
            except Exception:
                self._restore_pending(batch)
                raise
            flushed += sum(batch.values())

    def _flush_loop(self) -> None:
        while not self._stop.wait(self.flush_interval_seconds):
            try:
                self.flush()
            except Exception:
                # Counts remain pending. The request path must never fail because
                # optional evidence persistence is temporarily unavailable.
                continue

    def _pending_rows(self, since: date) -> dict[UsageKey, int]:
        with self._lock:
            return {
                key: count
                for key, count in self._pending.items()
                if date.fromisoformat(key.day) >= since
            }

    def snapshot(self, *, days: int = 30) -> dict[str, Any]:
        bounded_days = max(1, min(int(days), MAX_SNAPSHOT_DAYS))
        since = self._now().astimezone(timezone.utc).date() - timedelta(days=bounded_days - 1)
        combined: dict[UsageKey, int] = defaultdict(int)
        if self.durable:
            principal = urllib.parse.quote(self.principal_id, safe="")
            since_value = urllib.parse.quote(since.isoformat(), safe="")
            rows = self._request(
                "GET",
                "/rest/v1/wearabllm_protocol_usage_daily"
                f"?principal_id=eq.{principal}&day=gte.{since_value}"
                "&select=day,protocol_version,route_family,method,status_class,client_name,client_version,request_count"
                f"&order=day.desc&limit={MAX_SNAPSHOT_ROWS}",
            ) or []
            for row in rows:
                key = UsageKey(
                    day=str(row["day"]),
                    protocol_version=str(row["protocol_version"]),
                    route_family=str(row["route_family"]),
                    method=str(row["method"]),
                    status_class=str(row["status_class"]),
                    client_name=str(row["client_name"]),
                    client_version=str(row["client_version"]),
                )
                combined[key] += int(row["request_count"])
        pending_rows = self._pending_rows(since)
        for key, count in pending_rows.items():
            combined[key] += count
        rows = [
            key.public_dict(count)
            for key, count in sorted(
                combined.items(),
                key=lambda item: (
                    item[0].day,
                    item[0].protocol_version,
                    item[0].client_name,
                    item[0].route_family,
                    item[0].status_class,
                ),
                reverse=True,
            )
        ]
        return {
            "backend": self.backend,
            "days": bounded_days,
            "since": since.isoformat(),
            "rows": rows[:MAX_SNAPSHOT_ROWS],
            "pending_requests": sum(pending_rows.values()),
            "privacy": {
                "aggregate_only": True,
                "content_collected": False,
                "device_ids_collected": False,
                "query_parameters_collected": False,
            },
        }

    def close(self) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=min(self.flush_interval_seconds, 2.0))
        if self.durable:
            try:
                self.flush()
            except Exception:
                pass
