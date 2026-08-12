"""Application service boundary for WearabLLM bridge orchestration."""

from __future__ import annotations

import re
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from action_queue import normalize_sensor_manifest, validate_device_id
from bridge_contracts import (
    AssistantResult,
    InteractionInput,
    InteractionResult,
    QueryInput,
    QueryResult,
)


class ServiceValidationError(ValueError):
    """An expected application validation failure."""


class ServiceNotFoundError(LookupError):
    """An expected lookup failure."""


class ServiceUnavailableError(RuntimeError):
    """A configured application capability is unavailable."""


class ServicePermissionError(PermissionError):
    """A valid principal attempted an operation for another body."""


@dataclass(frozen=True, slots=True)
class ConversationTurnView:
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.payload, dict):
            raise ServiceValidationError("conversation turn must be an object")
        required = {"id", "device_id", "role", "content", "created_at"}
        if not required.issubset(self.payload):
            raise ServiceValidationError("conversation turn is missing normalized fields")
        object.__setattr__(self, "payload", deepcopy(self.payload))

    def to_legacy_dict(self) -> dict[str, Any]:
        return deepcopy(self.payload)


@dataclass(frozen=True, slots=True)
class ConversationView:
    conversation_backend: str
    session: dict[str, Any] | None
    active_session_id: str | None
    sessions: tuple[dict[str, Any], ...]
    turns: tuple[ConversationTurnView, ...]
    devices: tuple[dict[str, Any], ...]
    filter_device_id: str | None

    def to_legacy_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "conversation_backend": self.conversation_backend,
            "session": deepcopy(self.session),
            "active_session_id": self.active_session_id,
            "sessions": deepcopy(list(self.sessions)),
            "turns": [turn.to_legacy_dict() for turn in self.turns],
            "devices": deepcopy(list(self.devices)),
            "filter_device_id": self.filter_device_id,
        }


@dataclass(frozen=True, slots=True)
class AudioQueryResult:
    query: QueryResult
    saved_wav: Path | None
    wav_info: dict[str, Any]


class BridgeService:
    """Coordinate bridge capabilities independently of HTTP and provider setup."""

    def __init__(
        self,
        *,
        assistant_gateway: Callable[..., AssistantResult],
        action_queue: Any,
        conversation_store: Any | None,
        conversation_backend: str,
        history_provider: Callable[[], list[dict[str, Any]]],
        history_clearer: Callable[[], None],
        history_lock: Any,
        plain_text: Callable[[str], str],
        known_device_bodies: Iterable[Mapping[str, Any]],
        infrastructure_device_ids: Iterable[str],
        exception_sink: Callable[..., None] | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        presence_ttl_seconds: float = 30.0,
        device_presence: dict[str, dict[str, Any]] | None = None,
        device_presence_lock: Any | None = None,
        sensor_manifests: dict[str, dict[str, Any]] | None = None,
        sensor_manifests_lock: Any | None = None,
        debug_wav_saver: Callable[[bytes], Any | None] | None = None,
        wav_inspector: Callable[[bytes], dict[str, Any]] | None = None,
        transcriber: Callable[[bytes], str] | None = None,
        capture_recorder: Callable[..., None] | None = None,
    ) -> None:
        self.assistant_gateway = assistant_gateway
        self.action_queue = action_queue
        self.conversation_store = conversation_store
        self.conversation_backend = conversation_backend
        self.history_provider = history_provider
        self.history_clearer = history_clearer
        self.history_lock = history_lock
        self.plain_text = plain_text
        self.known_device_bodies = tuple(deepcopy(dict(body)) for body in known_device_bodies)
        self.infrastructure_device_ids = frozenset(infrastructure_device_ids)
        self.exception_sink = exception_sink
        self.monotonic_clock = monotonic_clock
        self.utc_now = utc_now
        self.presence_ttl_seconds = float(presence_ttl_seconds)
        self.device_presence = device_presence if device_presence is not None else {}
        self.device_presence_lock = device_presence_lock or threading.Lock()
        self.sensor_manifests = sensor_manifests if sensor_manifests is not None else {}
        self.sensor_manifests_lock = sensor_manifests_lock or threading.Lock()
        self.debug_wav_saver = debug_wav_saver
        self.wav_inspector = wav_inspector
        self.transcriber = transcriber
        self.capture_recorder = capture_recorder

    def _emit_exception(
        self,
        event: str,
        exc: BaseException,
        *,
        level: str = "error",
    ) -> None:
        if self.exception_sink:
            self.exception_sink(event, exc, level=level)

    def touch_device(self, device_id: str) -> None:
        if device_id in self.infrastructure_device_ids:
            return
        clean_device_id = validate_device_id(device_id)
        now = self.utc_now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        with self.device_presence_lock:
            self.device_presence[clean_device_id] = {
                "monotonic": self.monotonic_clock(),
                "last_seen_at": now,
            }

    def presence_for(self, device_id: str) -> tuple[bool, str | None]:
        with self.device_presence_lock:
            presence = self.device_presence.get(device_id)
            if not presence:
                return False, None
            online = (
                self.monotonic_clock() - float(presence["monotonic"])
                <= self.presence_ttl_seconds
            )
            return online, str(presence.get("last_seen_at") or "") or None

    def register_sensor_manifest(
        self,
        device_id: str,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = normalize_sensor_manifest(device_id, manifest)
        with self.sensor_manifests_lock:
            self.sensor_manifests[device_id] = normalized
        self.touch_device(device_id)
        return deepcopy(normalized)

    def sensor_catalog(self, device_id: str = "") -> list[dict[str, Any]]:
        target = validate_device_id(device_id) if device_id else ""
        with self.sensor_manifests_lock:
            manifests = [
                deepcopy(value)
                for key, value in self.sensor_manifests.items()
                if not target or key == target
            ]
        return sorted(manifests, key=lambda value: str(value.get("device_id", "")))

    def answer_query(self, query: QueryInput) -> QueryResult:
        if not isinstance(query, QueryInput):
            raise TypeError("query must be a QueryInput")
        self.touch_device(query.device_id)
        assistant = self.assistant_gateway(
            query.transcript,
            device_id=query.device_id,
            response_device_id=query.response_device_id,
        )
        response_device = query.response_device_id or query.device_id
        reply = (
            self.plain_text(assistant.reply)
            if response_device == "wearabllm-esp32"
            else assistant.reply
        )
        return QueryResult(
            command=assistant.command,
            reply=reply,
            transcript=query.transcript,
            audio_bytes=query.audio_bytes,
            saved_wav=str(query.saved_wav) if query.saved_wav else None,
            wav_info=query.wav_info,
            sources=tuple(assistant.activity.sources),
            tool_results=tuple(assistant.activity.tool_results),
            persistence=assistant.persistence,
        )

    def answer_audio_query(self, wav_bytes: bytes, *, device_id: str) -> AudioQueryResult:
        saver = self.debug_wav_saver
        inspector = self.wav_inspector
        transcriber = self.transcriber
        recorder = self.capture_recorder
        if saver is None or inspector is None or transcriber is None or recorder is None:
            raise ServiceUnavailableError("Audio query adapters are not configured")
        clean_device_id = validate_device_id(device_id)
        saved_wav = saver(wav_bytes)
        wav_info = inspector(wav_bytes)
        transcript = transcriber(wav_bytes)
        query = self.answer_query(
            QueryInput(
                transcript=transcript,
                device_id=clean_device_id,
                audio_bytes=len(wav_bytes),
                saved_wav=saved_wav,
                wav_info=wav_info,
            )
        )
        recorder(
            wav_bytes=len(wav_bytes),
            saved_wav=saved_wav,
            wav_info=wav_info,
            transcript=transcript,
            command=query.command,
        )
        return AudioQueryResult(query=query, saved_wav=saved_wav, wav_info=wav_info)

    def create_interaction(self, request: InteractionInput) -> InteractionResult:
        if not isinstance(request, InteractionInput):
            raise TypeError("request must be an InteractionInput")
        origin = validate_device_id(request.origin_device_id)
        target = validate_device_id(request.target_device_id)
        response_device = (
            validate_device_id(request.response_device_id)
            if request.response_device_id
            else origin
        )
        query = self.answer_query(
            QueryInput(
                transcript=request.transcript,
                device_id=origin,
                response_device_id=response_device,
            )
        )
        action, created = self.action_queue.create(
            origin_device_id=origin,
            target_device_id=target,
            transcript=query.transcript,
            command=query.command,
            reply=self.plain_text(query.reply) if target == "wearabllm-esp32" else query.reply,
            idempotency_key=request.idempotency_key,
        )
        return InteractionResult(query=query, action=action, action_created=created)

    def list_actions(self, *, target_device_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        return self.action_queue.list(target_device_id=target_device_id, limit=limit)

    def get_action(self, action_id: str) -> dict[str, Any]:
        action = self.action_queue.get(action_id)
        if not action:
            raise ServiceNotFoundError("Action not found")
        return action

    def claim_action(
        self,
        *,
        target_device_id: str,
    ) -> dict[str, Any] | None:
        target = validate_device_id(target_device_id)
        self.touch_device(target)
        return self.action_queue.claim_next(target)

    def acknowledge_action(
        self,
        *,
        target_device_id: str,
        action_id: str,
        status: str,
        error: str = "",
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target = validate_device_id(target_device_id)
        self.touch_device(target)
        previous = self.action_queue.get(action_id)
        try:
            action = self.action_queue.acknowledge(
                target,
                action_id,
                status,
                error,
                result,
            )
        except LookupError as exc:
            raise ServiceNotFoundError("Action not found") from exc
        if (
            action.get("action_type") in {"temperature_measurement", "sensor_read"}
            and action.get("status") == "completed"
            and (previous or {}).get("status") != "completed"
        ):
            self.record_sensor_action(action)
        return action

    def record_sensor_action(self, action: dict[str, Any]) -> None:
        result = action.get("result") if isinstance(action.get("result"), dict) else None
        payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
        if not result or int(payload.get("schedule_count", 1)) <= 1:
            return
        if not self.conversation_store:
            return
        try:
            session = (
                self.conversation_store.active_session()
                or self.conversation_store.create_session()
            )
            index = int(payload.get("schedule_index", 1))
            count = int(payload.get("schedule_count", 1))
            readings = result.get("readings") if isinstance(result.get("readings"), list) else []
            summary = ", ".join(
                f"{row.get('sensor_id')}: {float(row.get('value')):.2f} {row.get('unit')}"
                for row in readings
                if isinstance(row, dict)
            )
            if not summary:
                return
            self.conversation_store.append(
                str(session["id"]),
                "ducati-temp-sensor",
                "assistant",
                f"Sensor loop {index}/{count}: {summary}.",
                metadata={
                    "sensor_reading": result,
                    "sensor_schedule_id": payload.get("schedule_id"),
                    "sensor_action_id": action.get("id"),
                },
            )
        except Exception as exc:
            self._emit_exception("bridge.sensor_persistence_failed", exc, level="warning")

    def start_new_conversation(self) -> dict[str, Any]:
        ended_session_id = None
        saved_turns = 0
        with self.history_lock:
            if self.conversation_store:
                active = self.conversation_store.active_session()
                active_turns = (
                    self.conversation_store.turns(str(active["id"])) if active else []
                )
                if active and active_turns:
                    self.conversation_store.end_session(active)
                    ended_session_id = str(active["id"])
                    saved_turns = len(active_turns)
                    session = self.conversation_store.create_session()
                elif active:
                    session = active
                else:
                    session = self.conversation_store.create_session()
            else:
                session = None
            self.history_clearer()
        return {
            "ok": True,
            "history_messages": 0,
            "ended_session_id": ended_session_id,
            "saved_turns": saved_turns,
            "active_session_id": str(session["id"]) if session else None,
            "session": session,
        }

    def clear_history(self) -> None:
        with self.history_lock:
            if self.conversation_store:
                self.conversation_store.clear()
            self.history_clearer()

    def prepare_active_session(
        self,
        *,
        summarize: Callable[[list[dict[str, Any]]], str],
        extract_memories: Callable[[str], Any],
    ) -> str:
        if not self.conversation_store:
            return ""
        session = self.conversation_store.active_session()
        if session and self.conversation_store.session_expired(session):
            summary = ""
            try:
                turns = self.conversation_store.turns(str(session["id"]))
                summary = summarize(turns)
                extract_memories(summary)
            except Exception as exc:
                self._emit_exception(
                    "bridge.session_consolidation_failed",
                    exc,
                    level="warning",
                )
            self.conversation_store.archive(session, summary)
            session = None
        if not session:
            session = self.conversation_store.create_session()
        return str(session["id"])

    @staticmethod
    def _validated_session_id(session_id: str) -> str:
        clean_session_id = session_id.strip().lower()
        if not re.fullmatch(r"[a-f0-9-]{36}", clean_session_id):
            raise ServiceValidationError("Invalid conversation session ID")
        return clean_session_id

    def archive_conversation(self, session_id: str) -> dict[str, Any]:
        clean_session_id = self._validated_session_id(session_id)
        if not self.conversation_store:
            raise ServiceUnavailableError("Conversation persistence is not configured")
        with self.history_lock:
            sessions = self.conversation_store.list_sessions(limit=100)
            session = next(
                (
                    item
                    for item in sessions
                    if str(item.get("id", "")).lower() == clean_session_id
                ),
                None,
            )
            if not session:
                raise ServiceNotFoundError("Conversation session not found")
            active = self.conversation_store.active_session()
            was_active = bool(
                active and str(active.get("id", "")).lower() == clean_session_id
            )
            already_archived = bool(session.get("archived_at"))
            archived_turns = (
                0 if already_archived else self.conversation_store.archive(session)
            )
            replacement = None
            if was_active:
                replacement = self.conversation_store.create_session()
                self.history_clearer()
        return {
            "ok": True,
            "archived_session_id": clean_session_id,
            "archived_turns": archived_turns,
            "already_archived": already_archived,
            "active_session_id": (
                str(replacement["id"])
                if replacement
                else str(active["id"])
                if active and not was_active
                else None
            ),
            "session": replacement,
        }

    def rename_conversation(self, session_id: str, title: str) -> dict[str, Any]:
        clean_session_id = self._validated_session_id(session_id)
        if not self.conversation_store:
            raise ServiceUnavailableError("Conversation persistence is not configured")
        session = self.conversation_store.rename(clean_session_id, title)
        return {"ok": True, "session": session}

    def conversation_view(
        self,
        *,
        device_id: str | None = None,
        session_id: str | None = None,
        limit: int = 200,
    ) -> ConversationView:
        limit = max(1, min(int(limit), 500))
        filter_device = (device_id or "").strip() or None
        requested_session = (session_id or "").strip() or None

        if self.conversation_store:
            active = self.conversation_store.active_session()
            sessions = self.conversation_store.list_sessions(limit=20)
            target_session = None
            if requested_session:
                target_session = next(
                    (
                        item
                        for item in sessions
                        if str(item.get("id")) == requested_session
                    ),
                    None,
                )
                if (
                    target_session is None
                    and active
                    and str(active.get("id")) == requested_session
                ):
                    target_session = active
            else:
                target_session = active

            raw_turns = (
                self.conversation_store.turns(str(target_session["id"]))
                if target_session
                else []
            )
            turns = [self._normalize_turn(turn) for turn in raw_turns]
            if filter_device:
                turns = [
                    turn
                    for turn in turns
                    if str(turn.payload.get("device_id", "")) == filter_device
                ]
            if len(turns) > limit:
                turns = turns[-limit:]
            seen_devices = {
                str(turn.payload.get("device_id", "")).strip()
                for turn in turns
                if str(turn.payload.get("device_id", "")).strip()
            }
            if active and not filter_device:
                seen_devices.update(
                    self.conversation_store.list_device_ids(str(active["id"]))
                )
            return ConversationView(
                conversation_backend=self.conversation_backend,
                session=target_session,
                active_session_id=str(active["id"]) if active else None,
                sessions=tuple(deepcopy(sessions)),
                turns=tuple(turns),
                devices=tuple(self.device_catalog(seen_devices)),
                filter_device_id=filter_device,
            )

        with self.history_lock:
            turns = [
                self._normalize_turn(
                    {
                        "id": index + 1,
                        "device_id": message.get("device_id") or "wearabllm-unknown",
                        "role": message.get("role", "user"),
                        "content": message.get("content", ""),
                        "created_at": None,
                    }
                )
                for index, message in enumerate(self.history_provider()[-limit:])
            ]
            if filter_device:
                turns = [
                    turn
                    for turn in turns
                    if turn.payload["device_id"] == filter_device
                ]
            return ConversationView(
                conversation_backend="local",
                session=None,
                active_session_id=None,
                sessions=(),
                turns=tuple(turns),
                devices=tuple(
                    self.device_catalog(
                        {
                            str(turn.payload.get("device_id", "")).strip()
                            for turn in turns
                            if str(turn.payload.get("device_id", "")).strip()
                        }
                    )
                ),
                filter_device_id=filter_device,
            )

    def conversation_snapshot(self, **kwargs: Any) -> dict[str, Any]:
        return self.conversation_view(**kwargs).to_legacy_dict()

    def _normalize_turn(self, turn: Mapping[str, Any]) -> ConversationTurnView:
        payload = deepcopy(dict(turn))
        device_id = str(payload.get("device_id", "")).strip() or "wearabllm-unknown"
        if device_id in self.infrastructure_device_ids:
            device_id = "web-console"
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            payload["metadata"] = {
                key: value for key, value in metadata.items() if key != "model_tool_context"
            }
        payload.update(
            {
                "id": payload.get("id"),
                "device_id": device_id,
                "role": payload.get("role", "user"),
                "content": payload.get("content", ""),
                "created_at": payload.get("created_at"),
            }
        )
        return ConversationTurnView(payload)

    def device_catalog(self, seen_device_ids: set[str]) -> list[dict[str, Any]]:
        seen_device_ids = seen_device_ids - self.infrastructure_device_ids
        catalog: list[dict[str, Any]] = []
        known_ids = {str(item["id"]) for item in self.known_device_bodies}
        for body in self.known_device_bodies:
            entry = deepcopy(body)
            online, last_seen_at = self.presence_for(str(body["id"]))
            entry["seen"] = online
            entry["online"] = online
            entry["last_seen_at"] = last_seen_at
            catalog.append(entry)
        for device_id in sorted(seen_device_ids):
            if device_id in known_ids:
                continue
            online, last_seen_at = self.presence_for(device_id)
            catalog.append(
                {
                    "id": device_id,
                    "label": device_id,
                    "kind": "custom",
                    "status": "active",
                    "description": "Discovered device body",
                    "seen": online,
                    "online": online,
                    "last_seen_at": last_seen_at,
                }
            )
        return catalog
